"""AML & financial crime investigation API."""

from __future__ import annotations

import uuid
from datetime import UTC, datetime
from typing import Any, Literal

from fastapi import APIRouter, Depends, Query, Request
from pydantic import BaseModel, Field
from sqlalchemy import func, or_, select
from sqlalchemy.orm import Session

from bankverse.auth.deps import Principal, client_ip, control_db, require, tenant_db
from bankverse.core.errors import Conflict, NotFound, ValidationFailed
from bankverse.db.models.platform import MlModel, ModelVersion
from bankverse.db.models.tenant import Account, AmlAccountScore, AmlAlert, Case, CaseEvent, Customer, Transfer
from bankverse.ml import registry
from bankverse.modules.aml import service
from bankverse.services import audit
from bankverse.services.cases import new_case_reference

router = APIRouter(prefix="/aml", tags=["aml"])
OPEN = ("new", "investigating", "escalated")


def _audit(p: Principal, request: Request, action: str, **kw: Any) -> None:
    audit.record(action, org_id=p.org_id, actor_user_id=p.user_id, actor_type=p.kind, actor_label=p.label,
                 ip=client_ip(request), **kw)


@router.get("/overview")
def overview(p: Principal = Depends(require("aml.read")), db: Session = Depends(tenant_db),
             cdb: Session = Depends(control_db)) -> dict[str, Any]:
    by_status = dict(db.execute(select(AmlAlert.status, func.count()).group_by(AmlAlert.status)).all())
    by_band = dict(db.execute(select(AmlAlert.risk_band, func.count()).where(AmlAlert.status.in_(OPEN))
                              .group_by(AmlAlert.risk_band)).all())
    typologies = db.execute(select(func.jsonb_array_elements_text(AmlAlert.typologies).label("t"), func.count())
                            .where(AmlAlert.status.in_(OPEN)).group_by("t")).all()
    scored = db.execute(select(func.count()).select_from(AmlAccountScore)).scalar_one()
    volume = db.execute(select(func.count(), func.coalesce(func.sum(Transfer.amount), 0))).one()
    model = cdb.execute(select(MlModel).where(MlModel.name == service.MODEL_NAME)).scalar_one_or_none()
    model_info = None
    if model:
        dep = registry.current_deployment(cdb, model.id)
        mv = cdb.get(ModelVersion, dep.model_version_id) if dep else None
        model_info = {"status": "production" if mv else "not_deployed", "version": mv.version if mv else None,
                      "test_pr_auc": mv.metrics["test"]["pr_auc"] if mv else None, "policy": service.POLICY}
    return {"alerts_by_status": by_status, "open_by_band": by_band, "open_by_typology": {t: n for t, n in typologies},
            "accounts_scored": scored, "transfers": {"count": volume[0], "volume": round(float(volume[1]), 2)},
            "model": model_info,
            "disclaimer": "Une alerte LCB-FT est une aide à l'investigation, pas une preuve de blanchiment."}


@router.get("/alerts")
def list_alerts(p: Principal = Depends(require("aml.read")), db: Session = Depends(tenant_db),
                status: str | None = None, band: str | None = None, typology: str | None = None,
                q: str | None = Query(default=None, max_length=100), page: int = Query(default=1, ge=1),
                page_size: int = Query(default=25, le=200)) -> dict[str, Any]:
    query = (select(AmlAlert, Account, Customer).join(Account, Account.id == AmlAlert.account_id)
             .join(Customer, Customer.id == AmlAlert.customer_id))
    if status:
        sts = status.split(",")
        query = query.where(AmlAlert.status.in_([*sts, *(OPEN if "open" in sts else ())]))
    if band:
        query = query.where(AmlAlert.risk_band == band)
    if typology:
        query = query.where(AmlAlert.typologies.contains([typology]))
    if q:
        query = query.where(or_(Customer.full_name.ilike(f"%{q}%"), Customer.external_ref.ilike(f"%{q}%"),
                                Account.iban.ilike(f"%{q.replace(' ', '')}%")))
    total = db.execute(select(func.count()).select_from(query.subquery())).scalar_one()
    rows = db.execute(query.order_by(AmlAlert.score.desc()).limit(page_size).offset((page - 1) * page_size)).all()
    return {"total": total, "items": [
        {"id": str(a.id), "score": a.score, "risk_band": a.risk_band, "status": a.status, "typologies": a.typologies,
         "rules": [h["rule"] for h in a.rule_hits], "ml_percentile": a.model_scores.get("ml_percentile"),
         "created_at": a.created_at, "case_id": str(a.case_id) if a.case_id else None,
         "account": {"id": str(acc.id), "iban": acc.iban, "type": acc.account_type},
         "customer": {"id": str(c.id), "name": c.full_name, "ref": c.external_ref, "type": c.customer_type,
                      "is_demo": c.is_demo}} for a, acc, c in rows]}


@router.get("/alerts/{alert_id}")
def get_alert(alert_id: uuid.UUID, p: Principal = Depends(require("aml.read")), db: Session = Depends(tenant_db)) -> dict[str, Any]:
    row = db.execute(select(AmlAlert, Account, Customer).join(Account, Account.id == AmlAlert.account_id)
                     .join(Customer, Customer.id == AmlAlert.customer_id)
                     .where(AmlAlert.id == alert_id, AmlAlert.org_id == p.org_id)).first()
    if row is None:
        raise NotFound("Alert not found")
    a, acc, c = row
    ids = [uuid.UUID(x) for x in a.evidence_transfer_ids]
    evid = db.execute(select(Transfer).where(Transfer.id.in_(ids)).order_by(Transfer.ts)).scalars().all()
    accs = {x.id: x for x in db.execute(select(Account).where(Account.id.in_(
        [t.src_account_id for t in evid if t.src_account_id] + [t.dst_account_id for t in evid if t.dst_account_id]))).scalars()}
    stats = db.execute(select(func.count(), func.coalesce(func.sum(Transfer.amount), 0))
                       .where(or_(Transfer.src_account_id == acc.id, Transfer.dst_account_id == acc.id))).one()

    def side(account_id: uuid.UUID | None, iban: str | None, name: str | None) -> dict[str, Any]:
        if account_id and account_id in accs:
            return {"account_id": str(account_id), "iban": accs[account_id].iban, "internal": True}
        return {"account_id": None, "iban": iban, "name": name, "internal": False}

    out = {
        "id": str(a.id), "score": a.score, "risk_band": a.risk_band, "status": a.status, "typologies": a.typologies,
        "rule_hits": a.rule_hits, "model_scores": a.model_scores, "created_at": a.created_at,
        "case_id": str(a.case_id) if a.case_id else None, "model_version": a.model_version,
        "account": {"id": str(acc.id), "iban": acc.iban, "type": acc.account_type, "opened_at": acc.opened_at,
                    "balance": float(acc.balance), "transfers": stats[0], "volume": round(float(stats[1]), 2)},
        "customer": {"id": str(c.id), "name": c.full_name, "ref": c.external_ref, "type": c.customer_type,
                     "segment": c.segment, "risk_rating": c.risk_rating, "kyc_status": c.kyc_status, "pep": c.pep,
                     "occupation": c.occupation, "annual_income": c.annual_income, "is_demo": c.is_demo},
        "evidence": [{"id": str(t.id), "ts": t.ts, "amount": t.amount, "channel": t.channel, "direction": t.direction,
                      "reference": t.reference, "from": side(t.src_account_id, None, None if t.src_account_id else t.counterparty_name),
                      "to": side(t.dst_account_id, t.counterparty_iban if not t.dst_account_id else None,
                                 t.counterparty_name if not t.dst_account_id else None),
                      "counterparty_country": t.counterparty_country} for t in evid],
        "disclaimer": "Une alerte LCB-FT n'est pas une preuve de blanchiment : elle signale des opérations à examiner.",
    }
    if p.org_kind == "demo":
        typ = db.execute(select(Transfer.sim_typology, func.count()).where(Transfer.id.in_(ids), Transfer.sim_typology.isnot(None))
                         .group_by(Transfer.sim_typology)).all()
        out["simulation_label"] = {"typologies": {k: v for k, v in typ}, "note": "Vérité terrain du simulateur DEMO (non utilisée par le modèle)."}
    return out


class AlertUpdate(BaseModel):
    status: Literal["investigating", "escalated", "closed_no_action"] | None = None
    note: str | None = Field(default=None, max_length=4000)
    assign_to_me: bool = False


@router.patch("/alerts/{alert_id}")
def update_alert(alert_id: uuid.UUID, body: AlertUpdate, request: Request, p: Principal = Depends(require("aml.investigate")),
                 db: Session = Depends(tenant_db)) -> dict[str, Any]:
    a = db.execute(select(AmlAlert).where(AmlAlert.id == alert_id, AmlAlert.org_id == p.org_id)).scalar_one_or_none()
    if a is None:
        raise NotFound("Alert not found")
    if a.status.startswith("closed"):
        raise Conflict("Alert is closed")
    if body.status == "closed_no_action" and not (body.note and len(body.note.strip()) >= 20):
        raise ValidationFailed("Closing without action requires a justification of at least 20 characters")
    before = a.status
    if body.assign_to_me:
        a.assigned_to = p.user_id
    if body.status:
        a.status = body.status
        if body.status.startswith("closed"):
            a.resolved_at = datetime.now(UTC)
    _audit(p, request, "aml.alert_updated", resource_type="aml_alert", resource_id=str(a.id),
           details={"from": before, "to": a.status, "note": body.note})
    return {"id": str(a.id), "status": a.status}


@router.post("/alerts/{alert_id}/case")
def open_case(alert_id: uuid.UUID, request: Request, p: Principal = Depends(require("aml.investigate")),
              db: Session = Depends(tenant_db)) -> dict[str, Any]:
    a = db.execute(select(AmlAlert).where(AmlAlert.id == alert_id, AmlAlert.org_id == p.org_id)).scalar_one_or_none()
    if a is None:
        raise NotFound("Alert not found")
    if a.case_id:
        c = db.get(Case, a.case_id)
        return {"case_id": str(a.case_id), "reference": c.reference if c else None}
    acc = db.get(Account, a.account_id)
    case_obj = Case(org_id=p.org_id, reference=new_case_reference(db, "AM"), case_type="aml",
                    title=f"Analyse LCB-FT — {', '.join(a.typologies)} — {acc.iban if acc else ''}", status="investigating",
                    priority="critical" if a.risk_band == "critical" else "high" if a.risk_band == "high" else "medium",
                    customer_id=a.customer_id, created_by=p.actor_id, assigned_to=p.user_id)
    db.add(case_obj)
    db.flush()
    a.case_id = case_obj.id
    a.status = "investigating"
    db.add(CaseEvent(org_id=p.org_id, case_id=case_obj.id, actor_id=p.user_id, actor_label=p.label, kind="link_alert",
                     payload={"alert_type": "aml", "alert_id": str(a.id), "score": a.score, "band": a.risk_band,
                              "typologies": a.typologies, "rules": [h["rule"] for h in a.rule_hits]}))
    _audit(p, request, "case.alert_linked", resource_type="case", resource_id=str(case_obj.id),
           details={"alert_id": str(a.id), "alert_type": "aml"})
    return {"case_id": str(case_obj.id), "reference": case_obj.reference}


@router.get("/accounts/{account_id}/network")
def network(account_id: uuid.UUID, hops: int = Query(default=2, ge=1, le=3), max_nodes: int = Query(default=50, ge=10, le=120),
            p: Principal = Depends(require("aml.read")), db: Session = Depends(tenant_db)) -> dict[str, Any]:
    if db.execute(select(Account.id).where(Account.id == account_id, Account.org_id == p.org_id)).first() is None:
        raise NotFound("Account not found")
    return service.ego_network(db, account_id, hops=hops, max_nodes=max_nodes)


@router.get("/accounts/{account_id}/transfers")
def account_transfers(account_id: uuid.UUID, p: Principal = Depends(require("aml.read")), db: Session = Depends(tenant_db),
                      limit: int = Query(default=200, le=1000)) -> list[dict[str, Any]]:
    if db.execute(select(Account.id).where(Account.id == account_id, Account.org_id == p.org_id)).first() is None:
        raise NotFound("Account not found")
    rows = db.execute(select(Transfer).where(or_(Transfer.src_account_id == account_id, Transfer.dst_account_id == account_id))
                      .order_by(Transfer.ts.desc()).limit(limit)).scalars()
    return [{"id": str(t.id), "ts": t.ts, "amount": t.amount, "channel": t.channel,
             "direction": "out" if t.src_account_id == account_id else "in", "reference": t.reference,
             "counterparty": t.counterparty_name or str(t.dst_account_id if t.src_account_id == account_id else t.src_account_id)[:8],
             "counterparty_country": t.counterparty_country} for t in rows]
