"""Credit application scoring (PD, grade, explanations, what-if). Decisions are always human."""

from __future__ import annotations

import time
import uuid
from datetime import UTC, datetime
from typing import Any

import numpy as np
import polars as pl
from sqlalchemy import insert, select
from sqlalchemy.orm import Session

from bankverse.core.metrics import MODEL_INFERENCES, MODEL_LATENCY
from bankverse.db.models.tenant import CreditApplication, InferenceLog
from bankverse.ml import registry
from bankverse.modules.credit.features import FEATURES, build, from_application, risk_grade

MODEL_NAME = "credit_default_pd"


def _predict(payload: dict[str, Any], X: np.ndarray) -> tuple[np.ndarray, np.ndarray | None]:
    model, cal, kind = payload["model"], payload["calibrator"], payload["kind"]
    raw = model.predict_proba(X)[:, 1]
    p = raw if cal is None else cal.predict(raw)
    contrib = None
    if kind == "lightgbm":
        contrib = model.booster_.predict(X, pred_contrib=True)[:, :-1]
    elif kind == "xgboost":
        import xgboost as xgb

        contrib = model.get_booster().predict(xgb.DMatrix(X), pred_contribs=True)[:, :-1]
    return np.clip(p, 0, 1), contrib


def score_features(features: dict[str, Any]) -> dict[str, Any]:
    loaded = registry.load_production(MODEL_NAME)
    t0 = time.perf_counter()
    feats = build(from_application(features)).collect()
    X = feats.select(FEATURES).to_numpy().astype(np.float32)
    p, contrib = _predict(loaded.payload, X)
    labels = loaded.payload["feature_labels"]
    factors = []
    if contrib is not None:
        order = np.argsort(-np.abs(contrib[0]))[:6]
        factors = [{"feature": FEATURES[j], "label": labels[FEATURES[j]], "value": round(float(X[0, j]), 4),
                    "contribution": round(float(contrib[0, j]), 4),
                    "direction": "increases_risk" if contrib[0, j] > 0 else "decreases_risk"} for j in order]
    latency = (time.perf_counter() - t0) * 1000
    MODEL_INFERENCES.labels(MODEL_NAME, loaded.tag, "ok").inc()
    MODEL_LATENCY.labels(MODEL_NAME).observe(latency / 1000)
    pd_ = float(p[0])
    return {"pd": round(pd_, 5), "grade": risk_grade(pd_), "factors": factors, "model_version": loaded.tag,
            "review_flag": pd_ >= loaded.payload["reference_cutoff"], "latency_ms": round(latency, 2),
            "features": {f: round(float(X[0, j]), 4) for j, f in enumerate(FEATURES)}}


def score_application(db: Session, org_id: uuid.UUID, app: CreditApplication) -> dict[str, Any]:
    res = score_features(app.features)
    app.pd = res["pd"]
    app.risk_grade = res["grade"]
    app.explanation = res["factors"]
    app.model_version = res["model_version"]
    app.scored_at = datetime.now(UTC)
    if app.status == "submitted":
        app.status = "scored"
    db.execute(insert(InferenceLog).values(org_id=org_id, model_name=MODEL_NAME, model_version=res["model_version"],
                                           entity_type="credit_application", entity_id=str(app.id), score=res["pd"],
                                           latency_ms=res["latency_ms"], features=res["features"]))
    return res


def batch_score_org(db: Session, org_id: uuid.UUID) -> dict[str, Any]:
    apps = db.execute(select(CreditApplication).where(CreditApplication.status.in_(("submitted", "scored")))).scalars().all()
    for a in apps:
        score_application(db, org_id, a)
    grades = pl.Series([a.risk_grade for a in apps]).value_counts().to_dicts() if apps else []
    return {"applications_scored": len(apps), "grades": grades}
