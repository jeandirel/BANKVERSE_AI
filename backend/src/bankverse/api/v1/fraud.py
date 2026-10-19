"""Fraud Operations Center API."""

from __future__ import annotations

import uuid
from datetime import UTC, datetime, timedelta
from typing import Any, Literal

from fastapi import APIRouter, Depends, Query, Request
from pydantic import BaseModel, Field
from sqlalchemy import and_, case, func, or_, select
from sqlalchemy.orm import Session

from bankverse.auth.deps import Principal, client_ip, control_db, require, tenant_db
from bankverse.core.errors import Conflict, NotFound, ValidationFailed
from bankverse.db.models.platform import MlModel, ModelVersion
from bankverse.db.models.tenant import Card, CardTransaction, Case, CaseEvent, Customer, FraudAlert
from bankverse.ml import registry
from bankverse.modules.fraud import service
from bankverse.services import audit, usage
from bankverse.services.cases import new_case_reference

router = APIRouter(prefix="/fraud", tags=["fraud"])

STATUSES = ("new", "in_review", "escalated", "closed_fraud", "closed_legitimate")


def _audit(p: Principal, request: Request, action: str, **kw: Any) -> None:
    audit.record(action, org_id=p.org_id, actor_user_id=p.user_id, actor_type=p.kind, actor_label=p.label,
                 ip=client_ip(request), **kw)


@router.get("/overview")
def overview(days: int = Query(default=30, ge=1, le=365), p: Principal = Depends(require("fraud.read")),
             db: Session = Depends(tenant_db), cdb: Session = Depends(control_db)) -> dict[str, Any]:
    since = datetime.now(UTC) - timedelta(days=days)
    by_status = dict(db.execute(select(FraudAlert.status, func.count()).group_by(FraudAlert.status)).all())
    by_band = dict(db.execute(select(FraudAlert.risk_band, func.count())
                              .where(FraudAlert.status.in_(("new", "in_review", "escalated")))
                              .group_by(FraudAlert.risk_band)).all())
    tx_stats = db.execute(select(func.count(), func.count(CardTransaction.risk_score), func.sum(CardTransaction.amount))
                          .where(CardTransaction.ts >= since)).one()
    daily = db.execute(
        select(func.date_trunc("day", CardTransaction.ts).label("d"), func.count(),
               func.count(FraudAlert.id))
        .select_from(CardTransaction).outerjoin(FraudAlert, FraudAlert.transaction_id == CardTransaction.id)
        .where(CardTransaction.ts >= since).group_by("d").order_by("d")
    ).all()
    overdue = db.execute(select(func.count()).select_from(FraudAlert).where(
        FraudAlert.status.in_(("new", "in_review")), FraudAlert.sla_due_at < datetime.now(UTC))).scalar_one()
    feedback = db.execute(select(FraudAlert.risk_band,
                                 func.count().filter(FraudAlert.analyst_label == "fraud"),
                                 func.count().filter(FraudAlert.analyst_label == "legitimate"))
                          .group_by(FraudAlert.risk_band)).all()
    top_mcc = db.execute(select(CardTransaction.mcc, func.count()).join(FraudAlert, FraudAlert.transaction_id == CardTransaction.id)
                         .group_by(CardTransaction.mcc).order_by(func.count().desc()).limit(8)).all()
    exposure = db.execute(select(func.coalesce(func.sum(FraudAlert.expected_loss), 0)).where(
        FraudAlert.status.in_(("new", "in_review", "escalated")))).scalar_one()
    model = _model_summary(cdb)
    demo = None
    if p.org_kind == "demo":
        # Simulation ground truth is only available in the DEMO bank and only shown as such.
        sim = db.execute(select(
            func.count().filter(CardTransaction.sim_is_fraud.is_(True)),
            func.count().filter(and_(CardTransaction.sim_is_fraud.is_(True), FraudAlert.id.isnot(None))),
            func.count(FraudAlert.id),
        ).select_from(CardTransaction).outerjoin(FraudAlert, FraudAlert.transaction_id == CardTransaction.id)).one()
        demo = {"simulated_fraud_transactions": sim[0], "simulated_fraud_alerted": sim[1], "alerts_total": sim[2],
                "note": "DEMO only: injected scenarios are known to the simulator, not to the model."}
    return {
        "period_days": days,
        "alerts_by_status": {s: by_status.get(s, 0) for s in STATUSES},
        "open_alerts_by_band": {b: by_band.get(b, 0) for b in ("critical", "high", "medium")},
        "transactions": {"count": tx_stats[0], "scored": tx_stats[1], "volume": round(float(tx_stats[2] or 0), 2)},
        "daily": [{"day": d.date().isoformat(), "transactions": n, "alerts": a} for d, n, a in daily],
        "overdue_alerts": overdue,
        "open_exposure": round(float(exposure), 2),
        "analyst_feedback": [{"band": b, "confirmed_fraud": f, "false_positive": fp} for b, f, fp in feedback],
        "top_alert_mcc": [{"mcc": m, "alerts": n} for m, n in top_mcc],
        "model": model,
        "demo_simulation": demo,
    }


def _model_summary(cdb: Session) -> dict[str, Any] | None:
    model = cdb.execute(select(MlModel).where(MlModel.name == service.MODEL_NAME)).scalar_one_or_none()
    if model is None:
        return None
    dep = registry.current_deployment(cdb, model.id)
    if dep is None:
        return {"status": "not_deployed"}
    mv = cdb.get(ModelVersion, dep.model_version_id)
    assert mv is not None
    m = mv.metrics
    return {"status": "production", "version": mv.version, "algorithm": mv.algorithm, "deployed_at": dep.created_at,
            "test_pr_auc": m["test"]["pr_auc"], "test_roc_auc": m["test"]["roc_auc"],
            "threshold": m["operational"]["threshold"], "trained_at": mv.created_at}


@router.get("/alerts")
def list_alerts(
    p: Principal = Depends(require("fraud.read")),
    db: Session = Depends(tenant_db),
    status: str | None = Query(default=None),
    band: str | None = Query(default=None, pattern="^(critical|high|medium)$"),
    q: str | None = Query(default=None, max_length=100),
    assigned: Literal["me", "unassigned"] | None = None,
    sort: Literal["priority", "score", "recent", "amount", "sla"] = "priority",
    page: int = Query(default=1, ge=1),
    page_size: int = Query(default=25, ge=1, le=200),
) -> dict[str, Any]:
    query = (select(FraudAlert, CardTransaction, Customer)
             .join(CardTransaction, CardTransaction.id == FraudAlert.transaction_id)
             .join(Customer, Customer.id == FraudAlert.customer_id))
    if status:
        sts = status.split(",")
        if not set(sts) <= set(STATUSES) | {"open"}:
            raise ValidationFailed("Unknown status")
        if "open" in sts:
            sts = [*sts, "new", "in_review", "escalated"]
        query = query.where(FraudAlert.status.in_(sts))
    if band:
        query = query.where(FraudAlert.risk_band == band)
    if assigned == "me":
        query = query.where(FraudAlert.assigned_to == p.user_id)
    elif assigned == "unassigned":
        query = query.where(FraudAlert.assigned_to.is_(None))
    if q:
        like = f"%{q}%"
        query = query.where(or_(Customer.full_name.ilike(like), Customer.external_ref.ilike(like),
                                CardTransaction.merchant_name.ilike(like)))
    total = db.execute(select(func.count()).select_from(query.subquery())).scalar_one()
    order = {
        "priority": (FraudAlert.priority.asc(), FraudAlert.score.desc()),
        "score": (FraudAlert.score.desc(),),
        "recent": (CardTransaction.ts.desc(),),
        "amount": (CardTransaction.amount.desc(),),
        "sla": (FraudAlert.sla_due_at.asc(),),
    }[sort]
    rows = db.execute(query.order_by(*order).limit(page_size).offset((page - 1) * page_size)).all()
    return {"total": total, "page": page, "page_size": page_size,
            "items": [_alert_row(a, t, c) for a, t, c in rows]}


def _alert_row(a: FraudAlert, t: CardTransaction, c: Customer) -> dict[str, Any]:
    return {
        "id": str(a.id), "status": a.status, "risk_band": a.risk_band, "priority": a.priority,
        "score": round(a.score, 4), "expected_loss": a.expected_loss, "created_at": a.created_at,
        "sla_due_at": a.sla_due_at, "assigned_to": str(a.assigned_to) if a.assigned_to else None,
        "analyst_label": a.analyst_label, "case_id": str(a.case_id) if a.case_id else None,
        "top_reason": a.reasons[0]["label"] if a.reasons else None,
        "transaction": {"id": str(t.id), "ts": t.ts, "amount": t.amount, "currency": t.currency,
                        "merchant": t.merchant_name, "merchant_city": t.merchant_city, "mcc": t.mcc,
                        "channel": t.use_chip, "errors": t.errors},
        "customer": {"id": str(c.id), "name": c.full_name, "ref": c.external_ref, "segment": c.segment,
                     "is_demo": c.is_demo},
    }


@router.get("/alerts/{alert_id}")
def get_alert(alert_id: uuid.UUID, p: Principal = Depends(require("fraud.read")),
              db: Session = Depends(tenant_db)) -> dict[str, Any]:
    row = db.execute(select(FraudAlert, CardTransaction, Customer)
                     .join(CardTransaction, CardTransaction.id == FraudAlert.transaction_id)
                     .join(Customer, Customer.id == FraudAlert.customer_id)
                     .where(FraudAlert.id == alert_id, FraudAlert.org_id == p.org_id)).first()
    if row is None:
        raise NotFound("Alert not found")
    a, t, c = row
    card = db.get(Card, t.card_id)
    history = db.execute(
        select(CardTransaction).where(CardTransaction.card_id == t.card_id,
                                      CardTransaction.ts >= t.ts - timedelta(days=30),
                                      CardTransaction.ts <= t.ts + timedelta(days=2))
        .order_by(CardTransaction.ts.desc()).limit(150)
    ).scalars().all()
    other_alerts = db.execute(select(FraudAlert.id, FraudAlert.status, FraudAlert.score, FraudAlert.created_at)
                              .join(CardTransaction, CardTransaction.id == FraudAlert.transaction_id)
                              .where(CardTransaction.card_id == t.card_id, FraudAlert.id != a.id)
                              .order_by(FraudAlert.created_at.desc()).limit(20)).all()
    base = _alert_row(a, t, c)
    base.update({
        "reasons": a.reasons,
        "resolution_note": a.resolution_note,
        "model_version": a.model_version,
        "card": {"id": str(card.id), "ref": card.card_ref, "brand": card.brand, "type": card.card_type,
                 "status": card.status, "home": card.home_state} if card else None,
        "customer_profile": {"city": c.city, "segment": c.segment, "risk_rating": c.risk_rating,
                             "kyc_status": c.kyc_status, "customer_since": c.onboarded_at, "pep": c.pep},
        "history": [{"id": str(h.id), "ts": h.ts, "amount": h.amount, "merchant": h.merchant_name,
                     "city": h.merchant_city, "mcc": h.mcc, "channel": h.use_chip, "errors": h.errors,
                     "risk_score": h.risk_score, "is_current": h.id == t.id} for h in history],
        "other_alerts_on_card": [{"id": str(i), "status": s, "score": round(sc, 4), "created_at": ca}
                                 for i, s, sc, ca in other_alerts],
    })
    if p.org_kind == "demo":
        base["simulation_label"] = {"scenario": t.sim_scenario, "is_fraud": t.sim_is_fraud,
                                    "note": "DEMO simulator ground truth (hidden from the model)."}
    return base


class AlertUpdateIn(BaseModel):
    status: Literal["in_review", "escalated", "closed_fraud", "closed_legitimate"] | None = None
    assign_to_me: bool = False
    note: str | None = Field(default=None, max_length=4000)


@router.patch("/alerts/{alert_id}")
def update_alert(alert_id: uuid.UUID, body: AlertUpdateIn, request: Request,
                 p: Principal = Depends(require("fraud.triage")), db: Session = Depends(tenant_db)) -> dict[str, Any]:
    a = db.execute(select(FraudAlert).where(FraudAlert.id == alert_id, FraudAlert.org_id == p.org_id)
                   .with_for_update()).scalar_one_or_none()
    if a is None:
        raise NotFound("Alert not found")
    if a.status in ("closed_fraud", "closed_legitimate") and body.status:
        raise Conflict("Alert is already closed")
    if body.status in ("closed_fraud", "closed_legitimate") and not (body.note and len(body.note.strip()) >= 10):
        raise ValidationFailed("A justification (>= 10 characters) is required to close an alert")
    before = a.status
    if body.assign_to_me:
        a.assigned_to = p.user_id
    if body.status:
        a.status = body.status
        if body.status.startswith("closed"):
            a.resolved_at = datetime.now(UTC)
            a.analyst_label = "fraud" if body.status == "closed_fraud" else "legitimate"
            a.resolution_note = body.note
        if a.assigned_to is None:
            a.assigned_to = p.user_id
    _audit(p, request, "fraud.alert_updated", resource_type="fraud_alert", resource_id=str(a.id),
           details={"from": before, "to": a.status, "note": body.note})
    return {"id": str(a.id), "status": a.status, "assigned_to": str(a.assigned_to) if a.assigned_to else None,
            "analyst_label": a.analyst_label}


class OpenCaseIn(BaseModel):
    title: str | None = Field(default=None, max_length=300)
    case_id: uuid.UUID | None = None


@router.post("/alerts/{alert_id}/case")
def open_case(alert_id: uuid.UUID, body: OpenCaseIn, request: Request, p: Principal = Depends(require("cases.write")),
              db: Session = Depends(tenant_db)) -> dict[str, Any]:
    if not p.can("fraud.read"):
        raise NotFound("Alert not found")
    a = db.execute(select(FraudAlert).where(FraudAlert.id == alert_id, FraudAlert.org_id == p.org_id)).scalar_one_or_none()
    if a is None:
        raise NotFound("Alert not found")
    t = db.get(CardTransaction, a.transaction_id)
    if body.case_id:
        case_obj = db.execute(select(Case).where(Case.id == body.case_id, Case.org_id == p.org_id)).scalar_one_or_none()
        if case_obj is None:
            raise NotFound("Case not found")
    else:
        case_obj = Case(org_id=p.org_id, reference=new_case_reference(db, "FR"), case_type="fraud",
                        title=body.title or f"Suspicion de fraude carte — {t.merchant_name if t else ''}",
                        status="investigating", priority={1: "critical", 2: "high"}.get(a.priority, "medium"),
                        customer_id=a.customer_id, created_by=p.actor_id, assigned_to=p.user_id)
        db.add(case_obj)
        db.flush()
        db.add(CaseEvent(org_id=p.org_id, case_id=case_obj.id, actor_id=p.user_id, actor_label=p.label,
                         kind="status_change", payload={"to": "investigating", "origin": "fraud_alert"}))
    a.case_id = case_obj.id
    if a.status == "new":
        a.status = "in_review"
    db.add(CaseEvent(org_id=p.org_id, case_id=case_obj.id, actor_id=p.user_id, actor_label=p.label, kind="link_alert",
                     payload={"alert_type": "fraud", "alert_id": str(a.id), "score": a.score, "band": a.risk_band,
                              "transaction_id": str(a.transaction_id), "reasons": a.reasons[:3]}))
    _audit(p, request, "case.alert_linked", resource_type="case", resource_id=str(case_obj.id),
           details={"alert_id": str(a.id), "alert_type": "fraud"})
    return {"case_id": str(case_obj.id), "reference": case_obj.reference}


class SimulateIn(BaseModel):
    card_id: uuid.UUID
    amount: float = Field(gt=-50_000, lt=50_000)
    mcc: int = Field(ge=1000, le=9999)
    channel: Literal["Chip Transaction", "Swipe Transaction", "Online Transaction"] = "Chip Transaction"
    merchant_name: str = Field(default="Commerçant simulé (DEMO)", max_length=120)
    merchant_city: str = Field(default="Paris", max_length=120)
    merchant_region: str | None = Field(default="Île-de-France", max_length=64)
    errors: str | None = Field(default=None, max_length=120)
    ts: datetime | None = None
    persist: bool = False


@router.post("/simulate")
def simulate(body: SimulateIn, request: Request, p: Principal = Depends(require("fraud.simulate")),
             db: Session = Depends(tenant_db)) -> dict[str, Any]:
    usage.consume(p.org_id, "fraud_scoring")
    card = db.execute(select(Card).where(Card.id == body.card_id, Card.org_id == p.org_id)).scalar_one_or_none()
    if card is None:
        raise NotFound("Card not found")
    if body.persist and p.org_kind != "demo":
        raise ValidationFailed("Simulated transactions can only be persisted in the DEMO organisation")
    ts = body.ts or db.execute(select(func.max(CardTransaction.ts))).scalar() or datetime.now(UTC)
    ts = ts.astimezone(UTC) if ts.tzinfo else ts.replace(tzinfo=UTC)
    online = body.channel == "Online Transaction"
    cand = {"id": uuid.uuid4(), "card_id": card.id, "ts": ts, "amount": body.amount, "use_chip": body.channel,
            "merchant_id": f"SIM-{abs(hash(body.merchant_name)) % 10**8}", "merchant_city": "ONLINE" if online else body.merchant_city,
            "merchant_state": None if online else body.merchant_region, "mcc": body.mcc, "errors": body.errors}
    result = service.score_one(db, p.org_id, cand)
    alert_id = None
    if body.persist:
        db.add(CardTransaction(id=cand["id"], org_id=p.org_id, card_id=card.id, customer_id=card.customer_id, ts=ts,
                               amount=body.amount, use_chip=body.channel, merchant_id=cand["merchant_id"],
                               merchant_name=body.merchant_name, merchant_city=cand["merchant_city"],
                               merchant_state=cand["merchant_state"], mcc=body.mcc, errors=body.errors,
                               risk_score=result["score"], scored_model_version=result["model_version"],
                               scored_at=datetime.now(UTC), is_demo=True, source="simulator",
                               sim_scenario="analyst_simulation"))
        db.flush()
        alert_id = service.create_alert(db, p.org_id, cand, card.customer_id, result)
    _audit(p, request, "fraud.simulated", resource_type="card", resource_id=str(card.id),
           details={"score": result["score"], "persisted": body.persist})
    return {**result, "transaction_id": str(cand["id"]) if body.persist else None,
            "alert_id": str(alert_id) if alert_id else None}


@router.get("/cards")
def list_cards(p: Principal = Depends(require("fraud.read")), db: Session = Depends(tenant_db),
               q: str | None = Query(default=None, max_length=100), limit: int = Query(default=20, le=100)) -> list[dict[str, Any]]:
    query = select(Card, Customer).join(Customer, Customer.id == Card.customer_id)
    if q:
        query = query.where(or_(Customer.full_name.ilike(f"%{q}%"), Customer.external_ref.ilike(f"%{q}%"),
                                Card.card_ref.ilike(f"%{q}%")))
    rows = db.execute(query.order_by(Customer.full_name).limit(limit)).all()
    return [{"id": str(c.id), "card_ref": c.card_ref, "customer": cu.full_name, "customer_ref": cu.external_ref,
             "city": cu.city, "home_region": c.home_state} for c, cu in rows]


@router.get("/transactions")
def list_transactions(p: Principal = Depends(require("fraud.read")), db: Session = Depends(tenant_db),
                      min_score: float = Query(default=0, ge=0, le=1), card_id: uuid.UUID | None = None,
                      page: int = Query(default=1, ge=1), page_size: int = Query(default=50, le=200)) -> dict[str, Any]:
    query = select(CardTransaction, Customer).join(Customer, Customer.id == CardTransaction.customer_id)
    if min_score > 0:
        query = query.where(CardTransaction.risk_score >= min_score)
    if card_id:
        query = query.where(CardTransaction.card_id == card_id)
    total = db.execute(select(func.count()).select_from(query.subquery())).scalar_one()
    rows = db.execute(query.order_by(CardTransaction.ts.desc()).limit(page_size).offset((page - 1) * page_size)).all()
    return {"total": total, "items": [
        {"id": str(t.id), "ts": t.ts, "amount": t.amount, "merchant": t.merchant_name, "city": t.merchant_city,
         "mcc": t.mcc, "channel": t.use_chip, "risk_score": t.risk_score, "customer": c.full_name,
         "source": t.source} for t, c in rows]}


@router.get("/model")
def model_details(p: Principal = Depends(require("fraud.read")), cdb: Session = Depends(control_db)) -> dict[str, Any]:
    model = cdb.execute(select(MlModel).where(MlModel.name == service.MODEL_NAME)).scalar_one_or_none()
    if model is None:
        raise NotFound("No fraud model trained yet")
    dep = registry.current_deployment(cdb, model.id)
    mv = cdb.get(ModelVersion, dep.model_version_id) if dep else None
    if mv is None:
        mv = cdb.execute(select(ModelVersion).where(ModelVersion.model_id == model.id)
                         .order_by(ModelVersion.version.desc()).limit(1)).scalar_one()
    return {"name": model.name, "version": mv.version, "status": mv.status, "algorithm": mv.algorithm,
            "metrics": mv.metrics, "model_card": mv.model_card, "features": mv.features,
            "trained_at": mv.created_at, "deployed": dep is not None}


_ = case
