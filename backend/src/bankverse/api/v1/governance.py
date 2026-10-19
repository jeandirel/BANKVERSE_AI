"""AI governance & MLOps API: model inventory, versions, model cards, four-eyes review, deployment and
rollback, live drift, incidents and the governance report.

Models are platform-wide (one registry serves every tenant). Their lifecycle (review, deploy, rollback) is
therefore reserved to members of the platform organisation holding the permission; banks see the inventory,
the evidence behind each model, the drift of their own traffic, and can declare incidents."""

from __future__ import annotations

import uuid
from datetime import UTC, datetime
from typing import Any, Literal

from fastapi import APIRouter, Depends, Query, Request
from pydantic import BaseModel, Field
from sqlalchemy import func, select
from sqlalchemy.orm import Session

from bankverse.auth.deps import Principal, client_ip, control_db, require, tenant_db
from bankverse.core.errors import Forbidden, NotFound
from bankverse.db.models.platform import DatasetVersion, MlModel, ModelDeployment, ModelVersion
from bankverse.db.models.tenant import AgentApproval, Incident
from bankverse.ml import registry
from bankverse.modules.governance import monitoring
from bankverse.services import audit, reports

router = APIRouter(tags=["governance"])
NOTICE = ("Les métriques proviennent des jeux de test indiqués dans chaque fiche. Aucune certification ni conformité réglementaire "
          "n'est revendiquée : une validation indépendante reste nécessaire avant tout usage en production.")


def _audit(p: Principal, request: Request, action: str, **kw: Any) -> None:
    audit.record(action, org_id=p.org_id, actor_user_id=p.user_id, actor_type=p.kind, actor_label=p.label, ip=client_ip(request), **kw)


def _lifecycle_guard(p: Principal) -> None:
    if not (p.org_kind == "platform" or p.is_platform_admin):
        raise Forbidden("Model lifecycle actions are performed by the platform MLOps team (models are shared by all tenants)",
                        code="platform_only")


@router.get("/models")
def models(p: Principal = Depends(require("models.read")), db: Session = Depends(tenant_db)) -> dict[str, Any]:
    return {"models": monitoring.models_overview(db), "notice": NOTICE,
            "can_manage_lifecycle": p.org_kind == "platform" or p.is_platform_admin}


def _model(cdb: Session, name: str) -> MlModel:
    m = cdb.execute(select(MlModel).where(MlModel.name == name)).scalar_one_or_none()
    if m is None:
        raise NotFound("Model not found")
    return m


def _strip(metrics: dict[str, Any]) -> dict[str, Any]:
    """Drops bulky curves from list views (kept in the version detail)."""
    return {k: v for k, v in metrics.items() if k not in ("pr_curve",)}


@router.get("/models/{name}")
def model_detail(name: str, p: Principal = Depends(require("models.read")), cdb: Session = Depends(control_db)) -> dict[str, Any]:
    m = _model(cdb, name)
    versions = cdb.execute(select(ModelVersion).where(ModelVersion.model_id == m.id).order_by(ModelVersion.version.desc())).scalars().all()
    deps = cdb.execute(select(ModelDeployment).where(ModelDeployment.model_id == m.id).order_by(ModelDeployment.created_at.desc())).scalars().all()
    vnum = {v.id: v.version for v in versions}
    ds_ids = {uuid.UUID(d) for v in versions for d in v.dataset_versions if d}
    ds = {d.id: d for d in cdb.execute(select(DatasetVersion).where(DatasetVersion.id.in_(ds_ids))).scalars()} if ds_ids else {}
    return {
        "name": m.name, "module": m.module, "task": m.task, "description": m.description, "risk_tier": m.risk_tier,
        "can_manage_lifecycle": p.org_kind == "platform" or p.is_platform_admin,
        "versions": [{"version": v.version, "status": v.status, "algorithm": v.algorithm, "created_at": v.created_at, "trained_by": v.trained_by,
                      "training_seconds": v.training_seconds, "git_commit": v.git_commit, "artifact_sha256": v.artifact_sha256,
                      "mlflow_run_id": v.mlflow_run_id, "headline": monitoring.headline(m, v.metrics), "params": v.params,
                      "features": v.features, "model_card": v.model_card, "approvals": monitoring.approvals_for(cdb, v),
                      "datasets": [{"id": str(d.id), "name": d.name, "layer": d.layer, "rows": d.rows, "fingerprint": d.fingerprint[:16]}
                                   for d in (ds.get(uuid.UUID(x)) for x in v.dataset_versions if x) if d]}
                     for v in versions],
        "deployments": [{"action": d.action, "version": vnum.get(d.model_version_id), "previous_version": vnum.get(d.previous_version_id) if d.previous_version_id else None,
                         "actor": d.actor_label, "reason": d.reason, "active": d.active, "at": d.created_at} for d in deps],
        "notice": NOTICE,
    }


@router.get("/models/{name}/versions/{version}/metrics")
def version_metrics(name: str, version: int, p: Principal = Depends(require("models.read")), cdb: Session = Depends(control_db)) -> dict[str, Any]:
    _model(cdb, name)
    mv = registry.get_version(cdb, name, version)
    return {"version": mv.version, "metrics": mv.metrics, "reference_profile_features": list(mv.reference_profile)}


class ReviewIn(BaseModel):
    decision: Literal["approved", "rejected"]
    comment: str = Field(min_length=10, max_length=4000)


@router.post("/models/{name}/versions/{version}/review")
def review(name: str, version: int, body: ReviewIn, request: Request, p: Principal = Depends(require("models.approve")),
           cdb: Session = Depends(control_db)) -> dict[str, Any]:
    _lifecycle_guard(p)
    mv = registry.get_version(cdb, name, version)
    reviewer = p.label
    registry.approve(cdb, mv, reviewer_id=p.actor_id, reviewer_label=reviewer, decision=body.decision, comment=body.comment)
    _audit(p, request, f"model.{body.decision}", resource_type="model_version", resource_id=str(mv.id),
           details={"model": name, "version": version, "comment": body.comment})
    return {"model": name, "version": version, "status": mv.status}


class DeployIn(BaseModel):
    reason: str = Field(min_length=5, max_length=2000)


@router.post("/models/{name}/versions/{version}/deploy")
def deploy(name: str, version: int, body: DeployIn, request: Request, p: Principal = Depends(require("models.deploy")),
           cdb: Session = Depends(control_db)) -> dict[str, Any]:
    _lifecycle_guard(p)
    mv = registry.get_version(cdb, name, version)
    dep = registry.deploy(cdb, mv, actor_label=p.label, reason=body.reason)
    _audit(p, request, "model.deployed", resource_type="model_version", resource_id=str(mv.id), details={"model": name, "version": version})
    return {"model": name, "version": version, "status": mv.status, "deployment_id": str(dep.id)}


@router.post("/models/{name}/rollback")
def rollback(name: str, body: DeployIn, request: Request, p: Principal = Depends(require("models.deploy")),
             cdb: Session = Depends(control_db)) -> dict[str, Any]:
    _lifecycle_guard(p)
    m = _model(cdb, name)
    dep = registry.rollback(cdb, m, actor_label=p.label, reason=body.reason)
    mv = cdb.get(ModelVersion, dep.model_version_id)
    _audit(p, request, "model.rolled_back", resource_type="model", resource_id=name, details={"to_version": mv.version if mv else None})
    return {"model": name, "rolled_back_to": mv.version if mv else None}


@router.get("/models/{name}/drift")
def model_drift(name: str, days: int = Query(default=30, ge=1, le=365), p: Principal = Depends(require("models.read")),
                db: Session = Depends(tenant_db)) -> dict[str, Any]:
    return {**monitoring.drift_for(db, name, days), "live": monitoring.live_stats(db, name, days)}


# ------------------------------------------------------------------------------------------------
# Incidents & overview
# ------------------------------------------------------------------------------------------------
class IncidentIn(BaseModel):
    title: str = Field(min_length=5, max_length=300)
    category: Literal["model_drift", "model_error", "data_quality", "pipeline", "security", "other"]
    severity: Literal["low", "medium", "high", "critical"]
    model_name: str | None = Field(default=None, max_length=120)
    description: str = Field(default="", max_length=8000)


class IncidentUpdate(BaseModel):
    status: Literal["open", "mitigating", "resolved"]
    resolution: str | None = Field(default=None, max_length=8000)


def _inc(i: Incident) -> dict[str, Any]:
    return {"id": str(i.id), "title": i.title, "category": i.category, "severity": i.severity, "status": i.status, "model_name": i.model_name,
            "description": i.description, "opened_by": i.opened_by, "created_at": i.created_at, "resolved_at": i.resolved_at,
            "resolution": i.resolution, "detected_automatically": i.detected_automatically}


@router.get("/governance/incidents")
def incidents(p: Principal = Depends(require("governance.read")), db: Session = Depends(tenant_db),
              status: str | None = Query(default=None, max_length=16)) -> list[dict[str, Any]]:
    q = select(Incident).order_by(Incident.created_at.desc()).limit(200)
    if status:
        q = q.where(Incident.status == status)
    return [_inc(i) for i in db.execute(q).scalars()]


@router.post("/governance/incidents", status_code=201)
def create_incident(body: IncidentIn, request: Request, p: Principal = Depends(require("governance.manage")),
                    db: Session = Depends(tenant_db)) -> dict[str, Any]:
    i = Incident(org_id=p.org_id, title=body.title, category=body.category, severity=body.severity, model_name=body.model_name,
                 description=body.description, opened_by=p.label)
    db.add(i)
    db.flush()
    _audit(p, request, "incident.opened", resource_type="incident", resource_id=str(i.id), details={"severity": body.severity})
    return _inc(i)


@router.patch("/governance/incidents/{incident_id}")
def update_incident(incident_id: uuid.UUID, body: IncidentUpdate, request: Request, p: Principal = Depends(require("governance.manage")),
                    db: Session = Depends(tenant_db)) -> dict[str, Any]:
    i = db.execute(select(Incident).where(Incident.id == incident_id, Incident.org_id == p.org_id)).scalar_one_or_none()
    if i is None:
        raise NotFound("Incident not found")
    i.status = body.status
    if body.resolution:
        i.resolution = body.resolution
    i.resolved_at = datetime.now(UTC) if body.status == "resolved" else None
    _audit(p, request, "incident.updated", resource_type="incident", resource_id=str(i.id), details={"status": body.status})
    return _inc(i)


@router.get("/governance/overview")
def overview(p: Principal = Depends(require("governance.read")), db: Session = Depends(tenant_db), cdb: Session = Depends(control_db)) -> dict[str, Any]:
    inv = monitoring.models_overview(db)
    open_inc = db.execute(select(Incident.severity, func.count()).where(Incident.status != "resolved").group_by(Incident.severity)).all()
    pending_agent = db.execute(select(func.count()).select_from(AgentApproval).where(AgentApproval.status == "pending")).scalar_one()
    credit = cdb.execute(select(ModelVersion).join(MlModel, MlModel.id == ModelVersion.model_id).join(
        ModelDeployment, (ModelDeployment.model_version_id == ModelVersion.id) & ModelDeployment.active.is_(True))
        .where(MlModel.name == "credit_default_pd")).scalar_one_or_none()
    fairness = None
    if credit is not None and "fairness" in credit.metrics:
        fairness = {attr: {"review_rate_gap": v.get("review_rate_gap"), "equal_opportunity_gap": v.get("equal_opportunity_gap"),
                           "calibration_gap_max": v.get("calibration_gap_max")} for attr, v in credit.metrics["fairness"].items()}
    controls = [
        {"key": "four_eyes_models", "label": "Quatre yeux sur les modèles", "status": "enforced", "evidence": "registry.approve refuse l'entraîneur comme réviseur"},
        {"key": "four_eyes_aml", "label": "Quatre yeux sur les décisions LCB-FT", "status": "enforced", "evidence": "décision de dossier AML par une 2e personne"},
        {"key": "human_in_the_loop_agents", "label": "Approbation humaine des actions d'agents", "status": "enforced", "evidence": f"{pending_agent} action(s) en attente"},
        {"key": "no_automated_credit", "label": "Aucune décision de crédit automatisée", "status": "enforced", "evidence": "décision réservée à l'analyste (credit.assess)"},
        {"key": "tenant_isolation", "label": "Isolation des tenants (RLS PostgreSQL forcée)", "status": "enforced", "evidence": "tests d'intégration RLS"},
        {"key": "audit_chain", "label": "Journal d'audit chaîné et append-only", "status": "enforced", "evidence": "vérification par hachage"},
        {"key": "external_validation", "label": "Validation indépendante des modèles", "status": "not_done", "evidence": "à réaliser avant tout usage réel"},
    ]
    return {"models": {"total": len(inv), "in_production": sum(1 for m in inv if m["production"]),
                       "pending_review": sum(m["pending_review"] for m in inv),
                       "drift_alerts": [m["name"] for m in inv if (m.get("drift") or {}).get("status") == "significant"]},
            "incidents_open": {s: n for s, n in open_inc}, "agent_actions_pending": pending_agent, "fairness_credit": fairness,
            "controls": controls, "notice": NOTICE}


@router.post("/governance/report")
def governance_report(request: Request, p: Principal = Depends(require("reports.create")), db: Session = Depends(tenant_db)) -> dict[str, Any]:
    if not p.can("governance.read"):
        raise Forbidden("governance.read required")
    inv = monitoring.models_overview(db)
    incs = db.execute(select(Incident).order_by(Incident.created_at.desc()).limit(50)).scalars().all()
    rows = [["Modèle", "Version en production", "Indicateur (test)", "Inférences 30 j", "Dérive (PSI max)"]]
    for m in inv:
        prod = m["production"]
        h = (prod or {}).get("headline") or {}
        rows.append([m["name"], f"v{prod['version']} — {prod['algorithm']}" if prod else "aucune", f"{h.get('label', '')} {h.get('value', '')}",
                     str((m.get("live") or {}).get("inferences", "—")), f"{(m.get('drift') or {}).get('max_psi', '—')} ({(m.get('drift') or {}).get('status', '—')})"])
    inc_rows = [["Date", "Titre", "Gravité", "Statut"]] + [[i.created_at.strftime("%Y-%m-%d"), i.title, i.severity, i.status] for i in incs]
    pdf = reports.build_pdf(title="Rapport de gouvernance IA", subtitle="Inventaire des modèles, suivi en production et incidents", org_name=p.org_name,
                            author=p.label, is_demo=p.org_kind == "demo", sections=[
                                {"heading": "1. Avertissement", "paragraphs": [NOTICE]},
                                {"heading": "2. Inventaire des modèles", "table": rows},
                                {"heading": "3. Incidents", "table": inc_rows if len(inc_rows) > 1 else None,
                                 "paragraphs": [] if len(inc_rows) > 1 else ["Aucun incident déclaré."]},
                                {"heading": "4. Contrôles", "paragraphs": [
                                    "Quatre yeux sur l'approbation des modèles et sur les décisions LCB-FT ; approbation humaine des actions d'agents ; "
                                    "aucune décision de crédit automatisée ; isolation des tenants par RLS ; journal d'audit chaîné.",
                                    "Non réalisé : validation indépendante des modèles, revue juridique et conformité."]}])
    rep = reports.store_report(db, org_id=p.org_id, kind="governance", title="Rapport de gouvernance IA", pdf=pdf, created_by=p.actor_id,
                               source_type="governance", source_id=None)
    _audit(p, request, "report.generated", resource_type="report", resource_id=str(rep.id), details={"kind": "governance"})
    return {"report_id": str(rep.id), "title": rep.title}
