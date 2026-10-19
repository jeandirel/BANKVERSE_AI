"""Model inventory and live monitoring (per tenant): deployed version, headline test metric, inference volume,
error rate, latency and feature drift (PSI of recent inference inputs vs the training reference profile)."""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from typing import Any

import numpy as np
from sqlalchemy import func, select
from sqlalchemy.orm import Session

from bankverse.db.models.platform import MlModel, ModelApproval, ModelDeployment, ModelVersion
from bankverse.db.models.tenant import InferenceLog
from bankverse.db.session import control_session
from bankverse.ml import drift

HEADLINE = {
    "fraud": ("PR-AUC test", ("test", "pr_auc")),
    "aml": ("PR-AUC test", ("test", "pr_auc")),
    "credit": ("AUC test", ("test", "roc_auc")),
}


def headline(m: MlModel, metrics: dict[str, Any]) -> dict[str, Any] | None:
    try:
        if m.module in HEADLINE:
            label, path = HEADLINE[m.module]
            v: Any = metrics
            for k in path:
                v = v[k]
            return {"label": label, "value": round(float(v), 4)}
        if m.name == "customer_message_topics":
            return {"label": "Macro-F1 test (77 intents)", "value": metrics["intent"]["comparison"]["tfidf_logreg"]["macro_f1"]}
        if m.name == "marketing_conversion":
            return {"label": "Lift 1er décile", "value": metrics["lift_test"][0]["lift"]}
        if m.name == "macro_forecasting":
            nb = metrics["naive_beaten"]
            return {"label": "Séries où un modèle bat le naïf", "value": f"{sum(bool(x) for x in nb.values())}/{len(nb)}"}
        if m.name == "document_classifier":
            return {"label": "Précision (mise en page inédite)", "value": metrics["classification"]["test_unseen_layout"]["accuracy"]}
        if m.name == "copilot_retriever":
            return {"label": "Recall@3 test", "value": metrics["fact_retrieval"]["test"]["reranker_lambdarank"]["recall@3"]}
    except (KeyError, TypeError, IndexError, ValueError):
        return None
    return None


def inventory() -> list[dict[str, Any]]:
    with control_session() as cdb:
        out = []
        for m in cdb.execute(select(MlModel).order_by(MlModel.module, MlModel.name)).scalars():
            versions = cdb.execute(select(ModelVersion).where(ModelVersion.model_id == m.id).order_by(ModelVersion.version.desc())).scalars().all()
            dep = cdb.execute(select(ModelDeployment).where(ModelDeployment.model_id == m.id, ModelDeployment.active.is_(True))
                              .order_by(ModelDeployment.created_at.desc()).limit(1)).scalar_one_or_none()
            prod = next((v for v in versions if dep and v.id == dep.model_version_id), None)
            latest = versions[0] if versions else None
            pending = sum(1 for v in versions if v.status == "candidate")
            out.append({"name": m.name, "module": m.module, "task": m.task, "risk_tier": m.risk_tier, "description": m.description,
                        "versions": len(versions), "pending_review": pending,
                        "production": None if prod is None else {"version": prod.version, "algorithm": prod.algorithm, "deployed_at": dep.created_at,  # type: ignore[union-attr]
                                                                 "trained_by": prod.trained_by, "headline": headline(m, prod.metrics)},
                        "latest": None if latest is None else {"version": latest.version, "status": latest.status, "created_at": latest.created_at,
                                                               "headline": headline(m, latest.metrics)}})
        return out


def live_stats(db: Session, model_name: str, days: int = 30) -> dict[str, Any]:
    since = datetime.now(UTC) - timedelta(days=days)
    n, errors, lat = db.execute(select(func.count(), func.count().filter(InferenceLog.status == "error"), func.avg(InferenceLog.latency_ms))
                                .where(InferenceLog.model_name == model_name, InferenceLog.created_at >= since)).one()
    return {"inferences": n, "errors": errors, "error_rate": round(errors / n, 4) if n else None,
            "mean_latency_ms": round(float(lat), 2) if lat is not None else None, "window_days": days}


def drift_for(db: Session, model_name: str, days: int = 30, sample: int = 5000) -> dict[str, Any]:
    with control_session() as cdb:
        m = cdb.execute(select(MlModel).where(MlModel.name == model_name)).scalar_one_or_none()
        if m is None:
            return {"status": "unknown_model"}
        dep = cdb.execute(select(ModelDeployment).where(ModelDeployment.model_id == m.id, ModelDeployment.active.is_(True))
                          .order_by(ModelDeployment.created_at.desc()).limit(1)).scalar_one_or_none()
        if dep is None:
            return {"status": "not_deployed"}
        mv = cdb.get(ModelVersion, dep.model_version_id)
        assert mv is not None
        profile, tag = dict(mv.reference_profile), f"{model_name}:v{mv.version}"
    if not profile:
        return {"status": "no_reference_profile", "model_version": tag,
                "note": "Ce modèle n'a pas de profil de référence numérique (texte / séries) : la dérive se suit par ses métriques."}
    since = datetime.now(UTC) - timedelta(days=days)
    rows = db.execute(select(InferenceLog.features).where(InferenceLog.model_name == model_name, InferenceLog.model_version == tag,
                                                          InferenceLog.created_at >= since)
                      .order_by(InferenceLog.created_at.desc()).limit(sample)).scalars().all()
    current: dict[str, list[float]] = {}
    for feats in rows:
        for k, v in (feats or {}).items():
            if isinstance(v, (int, float)) and v is not None:
                current.setdefault(k, []).append(float(v))
    rep = drift.drift_report(profile, {k: np.asarray(v) for k, v in current.items()})
    rep.update({"model_version": tag, "samples": len(rows), "window_days": days})
    return rep


def models_overview(db: Session) -> list[dict[str, Any]]:
    out = []
    for item in inventory():
        if item["production"] is None:
            out.append({**item, "live": None, "drift": None})
            continue
        d = drift_for(db, item["name"])
        out.append({**item, "live": live_stats(db, item["name"]),
                    "drift": {k: d.get(k) for k in ("status", "max_psi", "samples", "n_features")} | {"top": (d.get("features") or [])[:3]}})
    return out


def approvals_for(cdb: Session, mv: ModelVersion) -> list[dict[str, Any]]:
    return [{"decision": a.decision, "reviewer": a.reviewer_label, "comment": a.comment, "at": a.created_at}
            for a in cdb.execute(select(ModelApproval).where(ModelApproval.model_version_id == mv.id).order_by(ModelApproval.created_at)).scalars()]
