"""Fraud scoring service: online (single transaction with card history) and batch scoring, alert
creation with explanations, analyst feedback. Uses only the *deployed* model version."""

from __future__ import annotations

import time
import uuid
from datetime import UTC, datetime, timedelta
from typing import Any

import numpy as np
import pandas as pd
import polars as pl
from sqlalchemy import insert, select, text
from sqlalchemy.orm import Session

from bankverse.core.metrics import ALERTS_CREATED, MODEL_INFERENCES, MODEL_LATENCY
from bankverse.db.models.tenant import CardTransaction, FraudAlert, InferenceLog
from bankverse.ml import registry
from bankverse.modules.fraud.features import BASE_COLUMNS, FEATURES, NUMERIC_FEATURES, compute_features

MODEL_NAME = "fraud_card_transactions"
FR_REGIONS = {"Île-de-France", "Auvergne-Rhône-Alpes", "Provence-Alpes-Côte d'Azur", "Occitanie", "Pays de la Loire",
              "Grand Est", "Nouvelle-Aquitaine", "Hauts-de-France", "Bretagne", "Bourgogne-Franche-Comté", "Normandie",
              "Centre-Val de Loire", "Corse"}
HOME_COUNTRY = "FR"
SLA_HOURS = {"critical": 2, "high": 8, "medium": 24}
HISTORY_DAYS = 31


def merchant_country(city: str | None, state: str | None) -> str:
    if city == "ONLINE":
        return "ONLINE"
    if state is None or state in FR_REGIONS:
        return HOME_COUNTRY
    return state


class FraudScorer:
    def __init__(self) -> None:
        self.loaded = registry.load_production(MODEL_NAME)
        self.payload = self.loaded.payload
        self.kind: str = self.payload["kind"]
        self.threshold: float = float(self.payload["threshold"])
        self.bands: dict[str, float] = self.payload["bands"]
        self.labels: dict[str, str] = self.payload.get("feature_labels", {})

    @property
    def tag(self) -> str:
        return self.loaded.tag

    def _frame(self, feats: pl.DataFrame) -> pd.DataFrame:
        X = feats.select(FEATURES).to_pandas()
        for c in NUMERIC_FEATURES:
            X[c] = X[c].astype(np.float32)
        X["mcc"] = pd.Categorical(X["mcc"].astype(int), categories=self.payload["mcc_categories"])
        return X

    def predict(self, feats: pl.DataFrame) -> tuple[np.ndarray, np.ndarray | None]:
        """Returns calibrated probabilities and per-feature contributions (log-odds) when available."""
        if feats.height == 0:
            return np.array([]), None
        model = self.payload["model"]
        if self.kind in ("lightgbm", "xgboost"):
            X = self._frame(feats)
            raw = model.predict_proba(X)[:, 1]
            if self.kind == "lightgbm":
                contrib = model.booster_.predict(X, pred_contrib=True)[:, :-1]
            else:
                import xgboost as xgb

                contrib = model.get_booster().predict(xgb.DMatrix(X, enable_categorical=True), pred_contribs=True)[:, :-1]
        else:
            pre = self.payload["preprocessor"]
            frame = feats.select(FEATURES).to_pandas()
            Xd = pre.compact(frame) if self.kind == "random_forest" else pre.dense(frame)
            raw = model.predict_proba(Xd)[:, 1]
            contrib = None
        return np.clip(self.payload["calibrator"].predict(raw), 0, 1), contrib

    def band(self, p: float) -> str | None:
        if p >= self.bands["critical"]:
            return "critical"
        if p >= self.bands["high"]:
            return "high"
        if p >= self.bands["medium"]:
            return "medium"
        return None

    def reasons(self, row: dict[str, Any], contrib_row: np.ndarray | None, k: int = 5) -> list[dict[str, Any]]:
        if contrib_row is None:
            return []
        order = np.argsort(-contrib_row)[:k]
        out = []
        for j in order:
            if contrib_row[j] <= 0:
                continue
            f = FEATURES[j]
            val = row.get(f)
            out.append({"feature": f, "label": self.labels.get(f, f), "value": _clean(val),
                        "contribution": round(float(contrib_row[j]), 4)})
        return out


_scorer: FraudScorer | None = None
_scorer_version: str | None = None


def get_scorer() -> FraudScorer:
    global _scorer, _scorer_version
    scorer = FraudScorer()
    if _scorer is None or _scorer_version != scorer.tag:
        _scorer, _scorer_version = scorer, scorer.tag
    return _scorer


def _clean(v: Any) -> Any:
    if isinstance(v, float | np.floating):
        return round(float(v), 4)
    if isinstance(v, np.integer):
        return int(v)
    return v


def tx_frame(rows: list[dict[str, Any]]) -> pl.DataFrame:
    """DB rows → BASE_COLUMNS frame (card_key = card id)."""
    return pl.DataFrame({
        "id": [str(r["id"]) for r in rows],
        "card_key": [str(r["card_id"]) for r in rows],
        "ts": [r["ts"].astimezone(UTC).replace(tzinfo=None) for r in rows],
        "amount": [float(r["amount"]) for r in rows],
        "use_chip": [r["use_chip"] for r in rows],
        "merchant_id": [r["merchant_id"] for r in rows],
        "merchant_city": [r["merchant_city"] for r in rows],
        "merchant_state": [r["merchant_state"] for r in rows],
        "merchant_country": [merchant_country(r["merchant_city"], r["merchant_state"]) for r in rows],
        "mcc": [int(r["mcc"]) for r in rows],
        "errors": [r["errors"] for r in rows],
    }, schema_overrides={"ts": pl.Datetime("us"), "errors": pl.Utf8, "merchant_state": pl.Utf8})


_TX_COLS = ("id", "card_id", "ts", "amount", "use_chip", "merchant_id", "merchant_city", "merchant_state", "mcc", "errors")


def card_history(db: Session, card_id: uuid.UUID, before: datetime) -> list[dict[str, Any]]:
    rows = db.execute(
        select(*[getattr(CardTransaction, c) for c in _TX_COLS])
        .where(CardTransaction.card_id == card_id, CardTransaction.ts >= before - timedelta(days=HISTORY_DAYS),
               CardTransaction.ts <= before)
        .order_by(CardTransaction.ts)
    ).all()
    return [dict(r._mapping) for r in rows]


def score_one(db: Session, org_id: uuid.UUID, candidate: dict[str, Any]) -> dict[str, Any]:
    """Scores a (possibly not yet persisted) transaction using the card's last 31 days."""
    scorer = get_scorer()
    t0 = time.perf_counter()
    hist = [h for h in card_history(db, candidate["card_id"], candidate["ts"]) if str(h["id"]) != str(candidate["id"])]
    frame = tx_frame([*hist, candidate])
    feats = compute_features(frame, HOME_COUNTRY).collect()
    row = feats.filter(pl.col("id") == str(candidate["id"]))
    p, contrib = scorer.predict(row)
    prob = float(p[0])
    latency = (time.perf_counter() - t0) * 1000
    MODEL_INFERENCES.labels(MODEL_NAME, scorer.tag, "ok").inc()
    MODEL_LATENCY.labels(MODEL_NAME).observe(latency / 1000)
    row_dict = row.to_dicts()[0]
    reasons = scorer.reasons(row_dict, contrib[0] if contrib is not None else None)
    db.execute(insert(InferenceLog).values(
        org_id=org_id, model_name=MODEL_NAME, model_version=scorer.tag, entity_type="card_transaction",
        entity_id=str(candidate["id"]), score=prob, latency_ms=latency,
        features={f: _clean(row_dict.get(f)) for f in NUMERIC_FEATURES}))
    return {"score": round(prob, 6), "band": scorer.band(prob), "threshold": scorer.threshold, "reasons": reasons,
            "model_version": scorer.tag, "latency_ms": round(latency, 2), "history_used": len(hist),
            "features": {f: _clean(row_dict.get(f)) for f in FEATURES}}


def create_alert(db: Session, org_id: uuid.UUID, tx: dict[str, Any], customer_id: uuid.UUID,
                 result: dict[str, Any]) -> uuid.UUID | None:
    band = result["band"]
    if band is None:
        return None
    existing = db.execute(select(FraudAlert.id).where(FraudAlert.transaction_id == tx["id"])).scalar_one_or_none()
    if existing:
        return existing
    alert_id = uuid.uuid4()
    db.execute(insert(FraudAlert).values(
        id=alert_id, org_id=org_id, transaction_id=tx["id"], customer_id=customer_id, score=result["score"],
        risk_band=band, priority={"critical": 1, "high": 2, "medium": 3}[band],
        expected_loss=round(result["score"] * abs(float(tx["amount"])), 2), model_version=result["model_version"],
        reasons=result["reasons"], status="new", sla_due_at=datetime.now(UTC) + timedelta(hours=SLA_HOURS[band])))
    ALERTS_CREATED.labels("fraud", band).inc()
    return alert_id


def batch_score_org(db: Session, org_id: uuid.UUID, *, since_days: int | None = None, log_sample: float = 0.05,
                    seed: int = 7) -> dict[str, Any]:
    """Scores every card transaction of the organisation that has >= 30 days of observable history.
    Used to (re)score the DEMO bank after a model deployment."""
    scorer = get_scorer()
    t0 = time.perf_counter()
    rows = [dict(r._mapping) for r in db.execute(select(*[getattr(CardTransaction, c) for c in _TX_COLS],
                                                        CardTransaction.customer_id)).all()]
    if not rows:
        return {"scored": 0}
    frame = tx_frame(rows).with_columns(customer_id=pl.Series([str(r["customer_id"]) for r in rows]))
    first_ts = frame.get_column("ts").min()
    feats = compute_features(frame, HOME_COUNTRY).collect()
    eligible = feats.filter(pl.col("ts") >= first_ts + timedelta(days=30))
    if since_days is not None:
        eligible = eligible.filter(pl.col("ts") >= datetime.now(UTC).replace(tzinfo=None) - timedelta(days=since_days))
    probs, contrib = scorer.predict(eligible)
    eligible = eligible.with_columns(score=pl.Series(probs))
    # Bulk update scores through a temporary table
    db.execute(text("CREATE TEMP TABLE tmp_scores (id uuid, score double precision) ON COMMIT DROP"))
    payload = [{"id": i, "score": float(s)} for i, s in zip(eligible.get_column("id").to_list(), probs, strict=True)]
    for k in range(0, len(payload), 10_000):
        db.execute(text("INSERT INTO tmp_scores (id, score) VALUES (:id, :score)"), payload[k:k + 10_000])
    db.execute(text("""UPDATE bank.card_transactions t SET risk_score = s.score, scored_model_version = :v,
                       scored_at = now() FROM tmp_scores s WHERE t.id = s.id"""), {"v": scorer.tag})
    # Alerts
    by_id = {str(r["id"]): r for r in rows}
    alerts = 0
    flagged = np.where(probs >= scorer.threshold)[0]
    erows = eligible.to_dicts()
    for j in flagged:
        r = erows[j]
        result = {"score": float(probs[j]), "band": scorer.band(float(probs[j])), "model_version": scorer.tag,
                  "reasons": scorer.reasons(r, contrib[j] if contrib is not None else None)}
        tx = by_id[r["id"]]
        if create_alert(db, org_id, {"id": tx["id"], "amount": tx["amount"]}, tx["customer_id"], result):
            alerts += 1
    # Inference log: all alerts + a random sample (drift monitoring)
    rng = np.random.default_rng(seed)
    sample_idx = set(np.where(rng.random(len(erows)) < log_sample)[0].tolist()) | set(flagged.tolist())
    logs = [{"org_id": org_id, "model_name": MODEL_NAME, "model_version": scorer.tag, "entity_type": "card_transaction",
             "entity_id": erows[j]["id"], "score": float(probs[j]), "latency_ms": None, "status": "ok",
             "features": {f: _clean(erows[j][f]) for f in NUMERIC_FEATURES},
             "created_at": erows[j]["ts"].replace(tzinfo=UTC)} for j in sorted(sample_idx)]
    for k in range(0, len(logs), 5000):
        db.execute(insert(InferenceLog), logs[k:k + 5000])
    MODEL_INFERENCES.labels(MODEL_NAME, scorer.tag, "ok").inc(len(erows))
    return {"model_version": scorer.tag, "scored": len(erows), "alerts_created": alerts,
            "threshold": scorer.threshold, "seconds": round(time.perf_counter() - t0, 1),
            "skipped_insufficient_history": len(rows) - len(erows)}


__all__ = ["BASE_COLUMNS", "FraudScorer", "batch_score_org", "create_alert", "get_scorer", "score_one"]
