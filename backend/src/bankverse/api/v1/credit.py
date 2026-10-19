"""Credit Risk Intelligence API (decision support — no automated credit decisions)."""

from __future__ import annotations

import copy
import uuid
from datetime import UTC, datetime
from typing import Any, Literal

from fastapi import APIRouter, Depends, Query, Request
from pydantic import BaseModel, Field, field_validator
from sqlalchemy import func, select
from sqlalchemy.orm import Session

from bankverse.auth.deps import Principal, client_ip, control_db, require, tenant_db
from bankverse.core.errors import Conflict, NotFound, ValidationFailed
from bankverse.db.models.platform import MlModel, ModelVersion
from bankverse.db.models.tenant import Case, CaseEvent, CreditApplication, Customer
from bankverse.ml import registry
from bankverse.modules.credit import service
from bankverse.modules.credit.features import GRADE_BOUNDS
from bankverse.services import audit, usage
from bankverse.services.cases import new_case_reference

router = APIRouter(prefix="/credit", tags=["credit"])


def _audit(p: Principal, request: Request, action: str, **kw: Any) -> None:
    audit.record(action, org_id=p.org_id, actor_user_id=p.user_id, actor_type=p.kind, actor_label=p.label,
                 ip=client_ip(request), **kw)


def _model(cdb: Session) -> ModelVersion | None:
    m = cdb.execute(select(MlModel).where(MlModel.name == service.MODEL_NAME)).scalar_one_or_none()
    if m is None:
        return None
    dep = registry.current_deployment(cdb, m.id)
    return cdb.get(ModelVersion, dep.model_version_id) if dep else None


@router.get("/overview")
def overview(p: Principal = Depends(require("credit.read")), db: Session = Depends(tenant_db),
             cdb: Session = Depends(control_db)) -> dict[str, Any]:
    by_status = dict(db.execute(select(CreditApplication.status, func.count()).group_by(CreditApplication.status)).all())
    by_grade = dict(db.execute(select(CreditApplication.risk_grade, func.count()).where(CreditApplication.risk_grade.isnot(None))
                               .group_by(CreditApplication.risk_grade)).all())
    by_decision = dict(db.execute(select(CreditApplication.analyst_decision, func.count())
                                  .where(CreditApplication.analyst_decision.isnot(None)).group_by(CreditApplication.analyst_decision)).all())
    avg_pd, exposure = db.execute(select(func.avg(CreditApplication.pd), func.sum(CreditApplication.requested_limit))).one()
    mv = _model(cdb)
    return {
        "applications_by_status": by_status, "by_grade": {g: by_grade.get(g, 0) for g in GRADE_BOUNDS},
        "grade_bounds": GRADE_BOUNDS, "decisions": by_decision, "average_pd": round(float(avg_pd or 0), 4),
        "requested_exposure": round(float(exposure or 0), 2),
        "model": None if mv is None else {"version": mv.version, "algorithm": mv.algorithm,
                                          "test_auc": mv.metrics["test"]["roc_auc"], "test_gini": mv.metrics["test"]["gini"],
                                          "test_brier": mv.metrics["test"]["brier"], "reference_cutoff": mv.metrics["reference_cutoff"]},
        "notice": "Aide à la décision : aucune décision d'octroi ou de refus n'est prise automatiquement.",
    }


@router.get("/applications")
def list_applications(p: Principal = Depends(require("credit.read")), db: Session = Depends(tenant_db),
                      status: str | None = None, grade: str | None = None, q: str | None = Query(default=None, max_length=100),
                      page: int = Query(default=1, ge=1), page_size: int = Query(default=25, le=200)) -> dict[str, Any]:
    query = select(CreditApplication, Customer).join(Customer, Customer.id == CreditApplication.customer_id)
    if status:
        query = query.where(CreditApplication.status.in_(status.split(",")))
    if grade:
        query = query.where(CreditApplication.risk_grade == grade)
    if q:
        query = query.where(Customer.full_name.ilike(f"%{q}%") | CreditApplication.reference.ilike(f"%{q}%"))
    total = db.execute(select(func.count()).select_from(query.subquery())).scalar_one()
    rows = db.execute(query.order_by(CreditApplication.pd.desc().nulls_last()).limit(page_size).offset((page - 1) * page_size)).all()
    return {"total": total, "items": [
        {"id": str(a.id), "reference": a.reference, "status": a.status, "product": a.product, "requested_limit": a.requested_limit,
         "pd": a.pd, "grade": a.risk_grade, "decision": a.analyst_decision, "created_at": a.created_at,
         "customer": {"id": str(c.id), "name": c.full_name, "ref": c.external_ref, "segment": c.segment, "is_demo": c.is_demo}}
        for a, c in rows]}


def _app(db: Session, p: Principal, app_id: uuid.UUID) -> CreditApplication:
    a = db.execute(select(CreditApplication).where(CreditApplication.id == app_id, CreditApplication.org_id == p.org_id)).scalar_one_or_none()
    if a is None:
        raise NotFound("Application not found")
    return a


@router.get("/applications/{app_id}")
def get_application(app_id: uuid.UUID, p: Principal = Depends(require("credit.read")), db: Session = Depends(tenant_db)) -> dict[str, Any]:
    a = _app(db, p, app_id)
    c = db.get(Customer, a.customer_id)
    return {"id": str(a.id), "reference": a.reference, "status": a.status, "product": a.product, "requested_limit": a.requested_limit,
            "features": a.features, "pd": a.pd, "grade": a.risk_grade, "explanation": a.explanation, "model_version": a.model_version,
            "scored_at": a.scored_at, "decision": a.analyst_decision, "rationale": a.analyst_rationale, "decided_at": a.decided_at,
            "grade_bounds": GRADE_BOUNDS,
            "customer": None if c is None else {"id": str(c.id), "name": c.full_name, "ref": c.external_ref, "segment": c.segment,
                                                "occupation": c.occupation, "annual_income": c.annual_income, "city": c.city,
                                                "customer_since": c.onboarded_at, "is_demo": c.is_demo}}


@router.post("/applications/{app_id}/score")
def rescore(app_id: uuid.UUID, request: Request, p: Principal = Depends(require("credit.assess")),
            db: Session = Depends(tenant_db)) -> dict[str, Any]:
    usage.consume(p.org_id, "credit_scoring")
    a = _app(db, p, app_id)
    res = service.score_application(db, p.org_id, a)
    _audit(p, request, "credit.scored", resource_type="credit_application", resource_id=str(a.id), details={"pd": res["pd"]})
    return res


class WhatIf(BaseModel):
    limit_bal: float | None = Field(default=None, gt=0, le=1e6)
    pay_status: list[int] | None = None
    bill_amt: list[float] | None = None
    pay_amt: list[float] | None = None

    @field_validator("pay_status")
    @classmethod
    def _ps(cls, v: list[int] | None) -> list[int] | None:
        if v is not None and (len(v) != 6 or any(x < -2 or x > 8 for x in v)):
            raise ValueError("pay_status: 6 values between -2 and 8")
        return v


@router.post("/applications/{app_id}/what-if")
def what_if(app_id: uuid.UUID, body: WhatIf, p: Principal = Depends(require("credit.read")),
            db: Session = Depends(tenant_db)) -> dict[str, Any]:
    usage.consume(p.org_id, "credit_scoring")
    a = _app(db, p, app_id)
    f = copy.deepcopy(a.features)
    for k, v in body.model_dump(exclude_none=True).items():
        if isinstance(v, list) and len(v) != 6:
            raise ValidationFailed(f"{k} must contain 6 monthly values")
        f[k] = v
    base = service.score_features(a.features)
    scen = service.score_features(f)
    return {"baseline": {"pd": base["pd"], "grade": base["grade"]}, "scenario": scen, "delta_pd": round(scen["pd"] - base["pd"], 5)}


class Decision(BaseModel):
    decision: Literal["approve", "decline", "request_information"]
    rationale: str = Field(min_length=30, max_length=8000)


@router.post("/applications/{app_id}/decision")
def decide(app_id: uuid.UUID, body: Decision, request: Request, p: Principal = Depends(require("credit.assess")),
           db: Session = Depends(tenant_db)) -> dict[str, Any]:
    a = _app(db, p, app_id)
    if a.status == "decided":
        raise Conflict("Application already decided")
    if a.pd is None:
        raise Conflict("Score the application before recording a decision")
    a.analyst_decision = body.decision
    a.analyst_rationale = body.rationale
    a.decided_by = p.user_id
    a.decided_at = datetime.now(UTC)
    a.status = "decided" if body.decision != "request_information" else "under_review"
    _audit(p, request, "credit.decision_recorded", resource_type="credit_application", resource_id=str(a.id),
           details={"decision": body.decision, "pd": a.pd, "grade": a.risk_grade, "human_decision": True})
    return {"status": a.status, "decision": a.analyst_decision}


@router.post("/applications/{app_id}/case")
def credit_case(app_id: uuid.UUID, request: Request, p: Principal = Depends(require("cases.write")),
                db: Session = Depends(tenant_db)) -> dict[str, Any]:
    if not p.can("credit.read"):
        raise NotFound("Application not found")
    a = _app(db, p, app_id)
    case_obj = Case(org_id=p.org_id, reference=new_case_reference(db, "CR"), case_type="credit",
                    title=f"Revue crédit {a.reference} — grade {a.risk_grade or '?'}", status="investigating",
                    priority="high" if (a.pd or 0) >= 0.35 else "medium", customer_id=a.customer_id,
                    created_by=p.actor_id, assigned_to=p.user_id)
    db.add(case_obj)
    db.flush()
    db.add(CaseEvent(org_id=p.org_id, case_id=case_obj.id, actor_id=p.user_id, actor_label=p.label, kind="evidence",
                     payload={"text": f"Demande {a.reference} : PD {a.pd:.3f} (grade {a.risk_grade}), plafond demandé "
                                      f"{a.requested_limit:,.0f} €. Facteurs : " +
                                      "; ".join(f"{x['label']} ({x['contribution']:+.2f})" for x in a.explanation[:4])}))
    _audit(p, request, "case.created", resource_type="case", resource_id=str(case_obj.id), details={"application": a.reference})
    return {"case_id": str(case_obj.id), "reference": case_obj.reference}


class NewApplication(BaseModel):
    customer_id: uuid.UUID
    requested_limit: float = Field(gt=0, le=1e6)
    pay_status: list[int]
    bill_amt: list[float]
    pay_amt: list[float]

    @field_validator("pay_status", "bill_amt", "pay_amt")
    @classmethod
    def _six(cls, v: list[Any]) -> list[Any]:
        if len(v) != 6:
            raise ValueError("6 monthly values required (most recent first)")
        return v


@router.post("/applications")
def create_application(body: NewApplication, request: Request, p: Principal = Depends(require("credit.assess")),
                       db: Session = Depends(tenant_db)) -> dict[str, Any]:
    c = db.execute(select(Customer).where(Customer.id == body.customer_id, Customer.org_id == p.org_id)).scalar_one_or_none()
    if c is None:
        raise NotFound("Customer not found")
    usage.consume(p.org_id, "credit_scoring")
    n = db.execute(select(func.count()).select_from(CreditApplication)).scalar_one()
    a = CreditApplication(org_id=p.org_id, reference=f"{'DEMO-' if p.org_kind == 'demo' else ''}CR-{n + 1:05d}", customer_id=c.id,
                          requested_limit=body.requested_limit, is_demo=p.org_kind == "demo",
                          features={"currency": "EUR", "limit_bal": body.requested_limit, "pay_status": body.pay_status,
                                    "bill_amt": body.bill_amt, "pay_amt": body.pay_amt})
    db.add(a)
    db.flush()
    res = service.score_application(db, p.org_id, a)
    _audit(p, request, "credit.application_created", resource_type="credit_application", resource_id=str(a.id),
           details={"pd": res["pd"]})
    return {"id": str(a.id), "reference": a.reference, **res}


@router.get("/model")
def model_details(p: Principal = Depends(require("credit.read")), cdb: Session = Depends(control_db)) -> dict[str, Any]:
    mv = _model(cdb)
    if mv is None:
        m = cdb.execute(select(MlModel).where(MlModel.name == service.MODEL_NAME)).scalar_one_or_none()
        if m is None:
            raise NotFound("No credit model trained yet")
        mv = cdb.execute(select(ModelVersion).where(ModelVersion.model_id == m.id).order_by(ModelVersion.version.desc()).limit(1)).scalar_one()
    return {"version": mv.version, "status": mv.status, "algorithm": mv.algorithm, "metrics": mv.metrics, "model_card": mv.model_card}
