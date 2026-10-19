"""AML scoring of a bank's transfer graph: graph-feature model (trained on AMLSim) + typology rules.

Alert policy (documented, versioned as ``aml-policy-v1``):
* an account is alerted if at least one typology rule fires, or if its model score ranks in the
  top 0.5 % of the bank's accounts;
* alert score = 1 − (1 − rule_score) × (1 − 0.5 × ml_top_decile), where ml_top_decile rescales the
  model percentile from [0.9, 1] to [0, 1]. Bands: critical ≥ 0.6, high ≥ 0.4, otherwise medium.
"""

from __future__ import annotations

import time
import uuid
from datetime import UTC, datetime
from typing import Any

import numpy as np
import polars as pl
from sqlalchemy import delete, insert, select
from sqlalchemy.orm import Session

from bankverse.core.metrics import ALERTS_CREATED, MODEL_INFERENCES
from bankverse.db.models.tenant import Account, AmlAccountScore, AmlAlert, InferenceLog, Transfer
from bankverse.ml import registry
from bankverse.modules.aml import rules
from bankverse.modules.aml.graph_features import STRUCTURAL, node_features

MODEL_NAME = "aml_account_risk"
POLICY = "aml-policy-v1"
ML_ALERT_PERCENTILE = 0.995


def load_transfers(db: Session) -> pl.DataFrame:
    rows = db.execute(select(Transfer.id, Transfer.src_account_id, Transfer.dst_account_id, Transfer.counterparty_iban,
                             Transfer.counterparty_country, Transfer.counterparty_name, Transfer.ts, Transfer.amount,
                             Transfer.channel, Transfer.direction, Transfer.reference)).all()
    if not rows:
        return pl.DataFrame()
    return pl.DataFrame([dict(r._mapping) for r in rows], infer_schema_length=None).with_columns(
        pl.col("id").cast(pl.Utf8), pl.col("src_account_id").cast(pl.Utf8), pl.col("dst_account_id").cast(pl.Utf8))


def graph_edges(tr: pl.DataFrame) -> pl.DataFrame:
    t0 = tr.get_column("ts").min()
    return tr.filter(~((pl.col("channel") == "cash") & pl.col("src_account_id").is_null())).select(
        src=pl.coalesce(pl.col("src_account_id"), pl.lit("ext:") + pl.col("counterparty_iban").fill_null("unknown")),
        dst=pl.coalesce(pl.col("dst_account_id"), pl.lit("ext:") + pl.col("counterparty_iban").fill_null("unknown")),
        amount=pl.col("amount"),
        step=((pl.col("ts") - t0).dt.total_days()).cast(pl.Int64),
    )


def gnn_scores(payload: dict[str, Any], feats: pl.DataFrame, edges: pl.DataFrame, nodes_internal: list[str]) -> np.ndarray:
    """GraphSAGE inference on the whole bank graph (internal + external nodes); returns internal-node scores."""
    import torch

    from bankverse.modules.aml.graph_features import edge_index
    from bankverse.modules.aml.train import GraphSAGE

    order = feats.get_column("node").to_list()
    x = torch.tensor(payload["gnn_scaler"].transform(feats.select(STRUCTURAL).to_numpy().astype(np.float32)))
    ei = torch.tensor(edge_index(edges, order))
    net = GraphSAGE(len(STRUCTURAL), payload.get("gnn_hidden", 64))
    net.load_state_dict(torch.load(payload["graphsage_state"], map_location="cpu"))
    net.eval()
    with torch.no_grad():
        p = torch.sigmoid(net(x, ei)).numpy()
    pos = {n: i for i, n in enumerate(order)}
    return np.array([p[pos[n]] for n in nodes_internal])


def batch_score_org(db: Session, org_id: uuid.UUID) -> dict[str, Any]:
    t0 = time.perf_counter()
    loaded = registry.load_production(MODEL_NAME)
    payload = loaded.payload
    tr = load_transfers(db)
    if tr.height == 0:
        return {"scored": 0}
    accounts = {str(a.id): a for a in db.execute(select(Account)).scalars()}
    edges = graph_edges(tr)
    feats = node_features(edges, list(accounts), window=payload["window"], pass_through_steps=payload["pass_through_steps"])
    internal = feats.filter(pl.col("node").is_in(list(accounts)))
    X = internal.select(STRUCTURAL).to_numpy().astype(np.float32)
    model = payload["model"]
    contrib = model.booster_.predict(X, pred_contrib=True)[:, :-1]  # interpretable companion model
    if payload["kind"] == "graphsage":
        prob = gnn_scores(payload, feats, edges, nodes_internal=internal.get_column("node").to_list())
    else:
        prob = model.predict_proba(X)[:, 1]
    pct = (np.argsort(np.argsort(prob)) + 1) / len(prob)
    hits = rules.evaluate(tr)
    nodes = internal.get_column("node").to_list()
    community = dict(zip(nodes, internal.get_column("community_id").to_list(), strict=True))

    db.execute(delete(AmlAccountScore).where(AmlAccountScore.model_version == loaded.tag))
    db.execute(delete(AmlAlert).where(AmlAlert.status == "new"))
    labels = payload.get("feature_labels", {})
    score_rows, alerts, logs = [], 0, []
    for i, acc_id in enumerate(nodes):
        fv = {f: round(float(X[i, j]), 4) for j, f in enumerate(STRUCTURAL)}
        score_rows.append({"id": uuid.uuid4(), "org_id": org_id, "account_id": uuid.UUID(acc_id), "model_version": loaded.tag,
                           "score": float(prob[i]), "anomaly_score": float(pct[i]), "features": fv,
                           "community_id": int(community.get(acc_id, -1))})
        logs.append({"org_id": org_id, "model_name": MODEL_NAME, "model_version": loaded.tag, "entity_type": "account",
                     "entity_id": acc_id, "score": float(prob[i]), "status": "ok", "features": fv})
        acc_hits = hits.get(acc_id, [])
        rule_score = rules.combine(acc_hits)
        ml_top = max(0.0, (float(pct[i]) - 0.9) / 0.1)
        if not acc_hits and pct[i] < ML_ALERT_PERCENTILE:
            continue
        score = round(1 - (1 - rule_score) * (1 - 0.5 * ml_top), 4)
        band = "critical" if score >= 0.6 else "high" if score >= 0.4 else "medium"
        evidence: list[str] = []
        for h in acc_hits:
            evidence.extend(h.transfer_ids)
        if not evidence:
            acc_tr = tr.filter((pl.col("src_account_id") == acc_id) | (pl.col("dst_account_id") == acc_id))
            evidence = acc_tr.sort("amount", descending=True).head(10).get_column("id").to_list()
        top = np.argsort(-contrib[i])[:5]
        acc = accounts[acc_id]
        db.execute(insert(AmlAlert).values(
            id=uuid.uuid4(), org_id=org_id, account_id=acc.id, customer_id=acc.customer_id, score=score, risk_band=band,
            typologies=sorted({h.typology for h in acc_hits}) or ["ml_anomaly"],
            rule_hits=[{"rule": h.rule, "typology": h.typology, "weight": h.weight, "detail": h.detail,
                        "transfers": len(h.transfer_ids)} for h in acc_hits],
            model_scores={"policy": POLICY, "ml_probability": round(float(prob[i]), 6), "ml_percentile": round(float(pct[i]), 4),
                          "rule_score": rule_score, "model_version": loaded.tag,
                          "top_factors": [{"feature": STRUCTURAL[j], "label": labels.get(STRUCTURAL[j], STRUCTURAL[j]),
                                           "value": fv[STRUCTURAL[j]], "contribution": round(float(contrib[i, j]), 4)}
                                          for j in top if contrib[i, j] > 0]},
            evidence_transfer_ids=list(dict.fromkeys(evidence))[:200], status="new", model_version=loaded.tag))
        ALERTS_CREATED.labels("aml", band).inc()
        alerts += 1
    for k in range(0, len(score_rows), 5000):
        db.execute(insert(AmlAccountScore), score_rows[k:k + 5000])
    for k in range(0, len(logs), 5000):
        db.execute(insert(InferenceLog), logs[k:k + 5000])
    MODEL_INFERENCES.labels(MODEL_NAME, loaded.tag, "ok").inc(len(nodes))
    return {"model_version": loaded.tag, "policy": POLICY, "accounts_scored": len(nodes), "graph_nodes": feats.height,
            "rule_hits": sum(len(v) for v in hits.values()), "alerts_created": alerts,
            "seconds": round(time.perf_counter() - t0, 1), "scored_at": datetime.now(UTC).isoformat()}


def ego_network(db: Session, account_id: uuid.UUID, hops: int = 2, max_nodes: int = 60) -> dict[str, Any]:
    tr = load_transfers(db)
    if tr.height == 0:
        return {"nodes": [], "edges": []}
    e = tr.with_columns(
        src=pl.coalesce(pl.col("src_account_id"), pl.lit("ext:") + pl.coalesce(pl.col("counterparty_iban"), pl.lit("cash"))),
        dst=pl.coalesce(pl.col("dst_account_id"), pl.lit("ext:") + pl.coalesce(pl.col("counterparty_iban"), pl.lit("cash"))),
    )
    frontier = {str(account_id)}
    keep = set(frontier)
    for _ in range(hops):
        nb = e.filter(pl.col("src").is_in(list(frontier)) | pl.col("dst").is_in(list(frontier)))
        # keep the strongest counterparties first to bound the visual graph
        strength = (pl.concat([nb.select(n=pl.col("src"), a=pl.col("amount")), nb.select(n=pl.col("dst"), a=pl.col("amount"))])
                    .group_by("n").agg(a=pl.col("a").sum()).sort("a", descending=True))
        new = [n for n in strength.get_column("n").to_list() if n not in keep][: max(0, max_nodes - len(keep))]
        keep.update(new)
        frontier = {n for n in new if not n.startswith("ext:")}
        if not frontier:
            break
    sub = e.filter(pl.col("src").is_in(list(keep)) & pl.col("dst").is_in(list(keep)))
    agg = sub.group_by(["src", "dst"]).agg(total=pl.col("amount").sum(), count=pl.len(), first=pl.col("ts").min(),
                                           last=pl.col("ts").max(), ids=pl.col("id"))
    internal_ids = [uuid.UUID(n) for n in keep if not n.startswith("ext:")]
    accs = {str(a.id): a for a in db.execute(select(Account).where(Account.id.in_(internal_ids))).scalars()}
    from bankverse.db.models.tenant import Customer

    custs = {c.id: c for c in db.execute(select(Customer).where(Customer.id.in_([a.customer_id for a in accs.values()]))).scalars()}
    scores = {str(s.account_id): s for s in db.execute(select(AmlAccountScore).where(AmlAccountScore.account_id.in_(internal_ids))).scalars()}
    alerts = {str(a.account_id): a for a in db.execute(select(AmlAlert).where(AmlAlert.account_id.in_(internal_ids))).scalars()}
    ext_names = {}
    for r in sub.filter(pl.col("counterparty_iban").is_not_null()).select("counterparty_iban", "counterparty_name", "counterparty_country").unique().iter_rows():
        ext_names[f"ext:{r[0]}"] = (r[1], r[2])
    nodes = []
    for n in keep:
        if n.startswith("ext:"):
            nm, ctry = ext_names.get(n, (None, None))
            nodes.append({"id": n, "kind": "external", "label": nm or ("Espèces" if n == "ext:cash" else n[4:14] + "…"),
                          "country": ctry, "iban": None if n == "ext:cash" else n[4:]})
        else:
            a = accs.get(n)
            c = custs.get(a.customer_id) if a else None
            s = scores.get(n)
            al = alerts.get(n)
            nodes.append({"id": n, "kind": "account", "label": c.full_name if c else n[:8], "iban": a.iban if a else None,
                          "customer_ref": c.external_ref if c else None, "ml_score": round(s.score, 4) if s else None,
                          "ml_percentile": round(s.anomaly_score, 4) if s and s.anomaly_score is not None else None,
                          "community": s.community_id if s else None, "alert_id": str(al.id) if al else None,
                          "alert_band": al.risk_band if al else None, "typologies": al.typologies if al else [],
                          "is_focus": n == str(account_id)})
    evidence = set()
    focus_alert = alerts.get(str(account_id))
    if focus_alert:
        evidence = set(focus_alert.evidence_transfer_ids)
    edges = [{"source": r["src"], "target": r["dst"], "total": round(r["total"], 2), "count": r["count"],
              "first": r["first"].isoformat(), "last": r["last"].isoformat(),
              "suspicious": bool(evidence.intersection(r["ids"]))} for r in agg.to_dicts()]
    return {"nodes": nodes, "edges": edges, "truncated": len(keep) >= max_nodes}
