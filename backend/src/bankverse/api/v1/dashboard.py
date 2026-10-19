"""Executive dashboard: one call, sections filtered by the caller's permissions and enabled modules."""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from typing import Any

from fastapi import APIRouter, Depends
from sqlalchemy import func, select
from sqlalchemy.orm import Session

from bankverse.auth.deps import Principal, control_db, require, tenant_db
from bankverse.db.models.tenant import (
    AgentApproval,
    AmlAlert,
    CardTransaction,
    Case,
    CreditApplication,
    CustomerMessage,
    Document,
    FraudAlert,
    Incident,
)
from bankverse.modules.governance import monitoring
from bankverse.services import usage

router = APIRouter(tags=["dashboard"])
OPEN_FRAUD = ("new", "in_review", "escalated")
OPEN_AML = ("new", "investigating", "escalated")


@router.get("/dashboard")
def dashboard(p: Principal = Depends(require("dashboard.read")), db: Session = Depends(tenant_db),
              cdb: Session = Depends(control_db)) -> dict[str, Any]:
    now = datetime.now(UTC)
    since = now - timedelta(days=30)
    out: dict[str, Any] = {"org": {"name": p.org_name, "kind": p.org_kind}, "generated_at": now}
    attention: list[dict[str, Any]] = []
    if p.can("fraud.read"):
        by_band = dict(db.execute(select(FraudAlert.risk_band, func.count()).where(FraudAlert.status.in_(OPEN_FRAUD)).group_by(FraudAlert.risk_band)).all())
        overdue = db.execute(select(func.count()).select_from(FraudAlert).where(FraudAlert.status.in_(("new", "in_review")),
                                                                                FraudAlert.sla_due_at < now)).scalar_one()
        exposure = db.execute(select(func.coalesce(func.sum(FraudAlert.expected_loss), 0)).where(FraudAlert.status.in_(OPEN_FRAUD))).scalar_one()
        daily = db.execute(select(func.date_trunc("day", FraudAlert.created_at).label("d"), func.count()).where(FraudAlert.created_at >= since)
                           .group_by("d").order_by("d")).all()
        tx = db.execute(select(func.count()).select_from(CardTransaction).where(CardTransaction.ts >= since)).scalar_one()
        out["fraud"] = {"open_by_band": {b: by_band.get(b, 0) for b in ("critical", "high", "medium")}, "overdue": overdue,
                        "open_exposure": round(float(exposure), 2), "transactions_30d": tx,
                        "daily_alerts": [{"day": d.date().isoformat(), "alerts": n} for d, n in daily]}
        if overdue:
            attention.append({"kind": "fraud_overdue", "count": overdue, "href": "/app/fraud"})
    if p.can("aml.read"):
        by_band = dict(db.execute(select(AmlAlert.risk_band, func.count()).where(AmlAlert.status.in_(OPEN_AML)).group_by(AmlAlert.risk_band)).all())
        typ = db.execute(select(func.jsonb_array_elements_text(AmlAlert.typologies).label("t"), func.count())
                         .where(AmlAlert.status.in_(OPEN_AML)).group_by("t").order_by(func.count().desc()).limit(6)).all()
        out["aml"] = {"open_by_band": {b: by_band.get(b, 0) for b in ("critical", "high", "medium")}, "top_typologies": [{"typology": t, "alerts": n} for t, n in typ]}
    if p.can("credit.read"):
        st = dict(db.execute(select(CreditApplication.status, func.count()).group_by(CreditApplication.status)).all())
        grades = dict(db.execute(select(CreditApplication.risk_grade, func.count()).where(CreditApplication.risk_grade.is_not(None))
                                 .group_by(CreditApplication.risk_grade)).all())
        avg_pd = db.execute(select(func.avg(CreditApplication.pd))).scalar_one()
        out["credit"] = {"by_status": st, "by_grade": {g: grades.get(g, 0) for g in "ABCDE"}, "average_pd": round(float(avg_pd), 4) if avg_pd else None}
    if p.can("documents.read"):
        to_review = db.execute(select(func.count()).select_from(Document).where(Document.status == "needs_review", Document.doc_type.is_distinct_from("kb"))).scalar_one()
        out["documents"] = {"needs_review": to_review}
        if to_review:
            attention.append({"kind": "documents_review", "count": to_review, "href": "/app/documents"})
    if p.can("cases.read"):
        st = dict(db.execute(select(Case.status, func.count()).group_by(Case.status)).all())
        out["cases"] = {"by_status": st, "open": sum(v for k, v in st.items() if k != "closed")}
    if p.can("customer.read"):
        complaints = db.execute(select(func.count()).select_from(CustomerMessage).where(CustomerMessage.is_complaint.is_(True),
                                                                                        CustomerMessage.received_at >= since)).scalar_one()
        topics = db.execute(select(CustomerMessage.topic_group, func.count()).where(CustomerMessage.received_at >= since,
                                                                                    CustomerMessage.topic_group.is_not(None))
                            .group_by(CustomerMessage.topic_group).order_by(func.count().desc()).limit(6)).all()
        out["customers"] = {"complaints_30d": complaints, "top_topics": [{"topic": t, "messages": n} for t, n in topics]}
    if p.can("agents.approve"):
        pend = db.execute(select(func.count()).select_from(AgentApproval).where(AgentApproval.status == "pending")).scalar_one()
        out["agents"] = {"pending_approvals": pend}
        if pend:
            attention.append({"kind": "agent_approvals", "count": pend, "href": "/app/agents"})
    if p.can("models.read"):
        inv = monitoring.inventory()
        out["models"] = {"in_production": [{"name": m["name"], "version": m["production"]["version"], "headline": m["production"]["headline"]}
                                           for m in inv if m["production"]],
                         "pending_review": sum(m["pending_review"] for m in inv), "total": len(inv)}
    if p.can("governance.read"):
        inc = db.execute(select(func.count()).select_from(Incident).where(Incident.status != "resolved")).scalar_one()
        out["incidents_open"] = inc
        if inc:
            attention.append({"kind": "incidents", "count": inc, "href": "/app/governance"})
    out["usage"] = usage.usage_for(cdb, p.org_id)
    out["attention"] = attention
    return out
