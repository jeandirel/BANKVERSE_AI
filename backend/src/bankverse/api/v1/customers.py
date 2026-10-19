"""Customer & banking intelligence API (customer 360, segments, message topics, campaign analytics)."""

from __future__ import annotations

import uuid
from typing import Any

from fastapi import APIRouter, Depends, Query, Request
from sqlalchemy import func, or_, select
from sqlalchemy.orm import Session

from bankverse.auth.deps import Principal, client_ip, control_db, require, tenant_db
from bankverse.core.errors import NotFound
from bankverse.db.models.platform import MlModel, ModelVersion
from bankverse.db.models.tenant import (
    Account,
    AmlAlert,
    Card,
    CardTransaction,
    CreditApplication,
    Customer,
    CustomerMessage,
    CustomerSegmentAssignment,
    FraudAlert,
)
from bankverse.ml import registry
from bankverse.modules.customer import service
from bankverse.services import audit

router = APIRouter(tags=["customers"])


@router.get("/customers")
def list_customers(p: Principal = Depends(require("customer.read")), db: Session = Depends(tenant_db),
                   q: str | None = Query(default=None, max_length=100), segment: int | None = None,
                   page: int = Query(default=1, ge=1), page_size: int = Query(default=25, le=200)) -> dict[str, Any]:
    query = select(Customer, CustomerSegmentAssignment.segment_label).outerjoin(
        CustomerSegmentAssignment, CustomerSegmentAssignment.customer_id == Customer.id)
    if q:
        query = query.where(or_(Customer.full_name.ilike(f"%{q}%"), Customer.external_ref.ilike(f"%{q}%"), Customer.city.ilike(f"%{q}%")))
    if segment is not None:
        query = query.where(CustomerSegmentAssignment.segment_id == segment)
    total = db.execute(select(func.count()).select_from(query.subquery())).scalar_one()
    rows = db.execute(query.order_by(Customer.full_name).limit(page_size).offset((page - 1) * page_size)).all()
    return {"total": total, "items": [
        {"id": str(c.id), "name": c.full_name, "ref": c.external_ref, "type": c.customer_type, "segment": c.segment, "city": c.city,
         "risk_rating": c.risk_rating, "kyc_status": c.kyc_status, "behaviour_segment": label, "is_demo": c.is_demo} for c, label in rows]}


@router.get("/customers/{customer_id}")
def customer_360(customer_id: uuid.UUID, request: Request, p: Principal = Depends(require("customer.read")),
                 db: Session = Depends(tenant_db)) -> dict[str, Any]:
    c = db.execute(select(Customer).where(Customer.id == customer_id, Customer.org_id == p.org_id)).scalar_one_or_none()
    if c is None:
        raise NotFound("Customer not found")
    audit.record("customer.viewed", org_id=p.org_id, actor_user_id=p.user_id, actor_label=p.label, ip=client_ip(request),
                 resource_type="customer", resource_id=str(c.id))
    accounts = db.execute(select(Account).where(Account.customer_id == c.id)).scalars().all()
    cards = db.execute(select(Card).where(Card.customer_id == c.id)).scalars().all()
    tx = db.execute(select(CardTransaction).where(CardTransaction.customer_id == c.id).order_by(CardTransaction.ts.desc()).limit(25)).scalars().all()
    seg = db.execute(select(CustomerSegmentAssignment).where(CustomerSegmentAssignment.customer_id == c.id)).scalar_one_or_none()
    msgs = db.execute(select(CustomerMessage).where(CustomerMessage.customer_id == c.id).order_by(CustomerMessage.received_at.desc()).limit(20)).scalars().all()
    out: dict[str, Any] = {
        "id": str(c.id), "name": c.full_name, "ref": c.external_ref, "type": c.customer_type, "segment": c.segment, "city": c.city,
        "postal_code": c.postal_code, "occupation": c.occupation, "annual_income": c.annual_income, "birth_year": c.birth_year,
        "risk_rating": c.risk_rating, "kyc_status": c.kyc_status, "pep": c.pep, "onboarded_at": c.onboarded_at, "is_demo": c.is_demo,
        "accounts": [{"id": str(a.id), "iban": a.iban, "type": a.account_type, "balance": float(a.balance), "status": a.status} for a in accounts],
        "cards": [{"id": str(k.id), "ref": k.card_ref, "brand": k.brand, "type": k.card_type, "status": k.status} for k in cards],
        "behaviour_segment": None if seg is None else {"label": seg.segment_label, "features": seg.features, "model_version": seg.model_version},
        "messages": [{"id": str(m.id), "received_at": m.received_at, "channel": m.channel, "text": m.text, "topic": m.predicted_topic,
                      "group": m.topic_group, "complaint": m.is_complaint, "confidence": m.confidence} for m in msgs],
    }
    if p.can("fraud.read"):
        out["recent_card_transactions"] = [{"id": str(t.id), "ts": t.ts, "amount": t.amount, "merchant": t.merchant_name, "mcc": t.mcc,
                                            "risk_score": t.risk_score} for t in tx]
        out["fraud_alerts"] = db.execute(select(func.count()).select_from(FraudAlert).where(FraudAlert.customer_id == c.id)).scalar_one()
    if p.can("aml.read"):
        out["aml_alerts"] = db.execute(select(func.count()).select_from(AmlAlert).where(AmlAlert.customer_id == c.id)).scalar_one()
    if p.can("credit.read"):
        out["credit_applications"] = [{"id": str(a.id), "reference": a.reference, "pd": a.pd, "grade": a.risk_grade, "status": a.status}
                                      for a in db.execute(select(CreditApplication).where(CreditApplication.customer_id == c.id)).scalars()]
    return out


@router.get("/customer-intelligence/overview")
def ci_overview(p: Principal = Depends(require("customer.read")), db: Session = Depends(tenant_db)) -> dict[str, Any]:
    segs = db.execute(select(CustomerSegmentAssignment.segment_id, CustomerSegmentAssignment.segment_label, func.count(),
                             CustomerSegmentAssignment.model_version)
                      .group_by(CustomerSegmentAssignment.segment_id, CustomerSegmentAssignment.segment_label,
                                CustomerSegmentAssignment.model_version).order_by(CustomerSegmentAssignment.segment_id)).all()
    profiles = {}
    for sid, _label, _n, _v in segs:
        rows = db.execute(select(CustomerSegmentAssignment.features).where(CustomerSegmentAssignment.segment_id == sid)).scalars().all()
        keys = list(rows[0].keys()) if rows else []
        profiles[sid] = {k: round(float(sorted(r[k] for r in rows)[len(rows) // 2]), 2) for k in keys}
    topics = db.execute(select(CustomerMessage.topic_group, func.count(), func.avg(CustomerMessage.confidence))
                        .where(CustomerMessage.topic_group.isnot(None)).group_by(CustomerMessage.topic_group)
                        .order_by(func.count().desc())).all()
    weekly = db.execute(select(func.date_trunc("week", CustomerMessage.received_at).label("w"), func.count(),
                               func.count().filter(CustomerMessage.is_complaint.is_(True)))
                        .group_by("w").order_by("w")).all()
    complaints = db.execute(select(func.count()).select_from(CustomerMessage).where(CustomerMessage.is_complaint.is_(True))).scalar_one()
    total_msgs = db.execute(select(func.count()).select_from(CustomerMessage)).scalar_one()
    return {
        "segments": [{"segment_id": s, "label": lab, "customers": n, "model_version": v, "profile": profiles.get(s, {})} for s, lab, n, v in segs],
        "topics": [{"group": g, "messages": n, "avg_confidence": round(float(c or 0), 3)} for g, n, c in topics],
        "weekly_messages": [{"week": w.date().isoformat(), "messages": n, "complaints": k} for w, n, k in weekly],
        "complaint_share": round(complaints / total_msgs, 4) if total_msgs else None, "messages": total_msgs,
    }


@router.get("/customer-intelligence/messages")
def messages(p: Principal = Depends(require("customer.read")), db: Session = Depends(tenant_db), group: str | None = None,
             complaints_only: bool = False, page: int = Query(default=1, ge=1), page_size: int = Query(default=25, le=100)) -> dict[str, Any]:
    q = select(CustomerMessage, Customer.full_name).outerjoin(Customer, Customer.id == CustomerMessage.customer_id)
    if group:
        q = q.where(CustomerMessage.topic_group == group)
    if complaints_only:
        q = q.where(CustomerMessage.is_complaint.is_(True))
    total = db.execute(select(func.count()).select_from(q.subquery())).scalar_one()
    rows = db.execute(q.order_by(CustomerMessage.received_at.desc()).limit(page_size).offset((page - 1) * page_size)).all()
    return {"total": total, "items": [{"id": str(m.id), "received_at": m.received_at, "channel": m.channel, "text": m.text, "customer": n,
                                       "topic": m.predicted_topic, "group": m.topic_group, "confidence": m.confidence,
                                       "complaint": m.is_complaint} for m, n in rows]}


@router.get("/customer-intelligence/search")
def search_messages(q: str = Query(min_length=3, max_length=300), p: Principal = Depends(require("customer.read")),
                    db: Session = Depends(tenant_db)) -> list[dict[str, Any]]:
    return service.semantic_search(db, q)


@router.get("/customer-intelligence/campaigns")
def campaigns(p: Principal = Depends(require("customer.read")), cdb: Session = Depends(control_db)) -> dict[str, Any]:
    m = cdb.execute(select(MlModel).where(MlModel.name == "marketing_conversion")).scalar_one_or_none()
    if m is None:
        raise NotFound("Campaign model not trained")
    dep = registry.current_deployment(cdb, m.id)
    mv = cdb.get(ModelVersion, dep.model_version_id) if dep else cdb.execute(
        select(ModelVersion).where(ModelVersion.model_id == m.id).order_by(ModelVersion.version.desc()).limit(1)).scalar_one()
    return {"source": "UCI Bank Marketing (Portuguese bank, 2008-2010, CC BY 4.0) — public dataset, not DEMO bank data",
            "version": mv.version, "status": mv.status, "metrics": mv.metrics, "model_card": mv.model_card}
