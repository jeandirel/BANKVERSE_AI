"""Investigation cases (shared workflow) and reports/exports."""

from __future__ import annotations

import csv
import io
import uuid
from datetime import UTC, datetime
from typing import Any, Literal

from fastapi import APIRouter, Depends, Query, Request
from fastapi.responses import Response
from pydantic import BaseModel, Field
from sqlalchemy import func, select
from sqlalchemy.orm import Session

from bankverse.auth.deps import Principal, client_ip, require, tenant_db
from bankverse.core.errors import Conflict, Forbidden, NotFound, ValidationFailed
from bankverse.core.storage import assert_key_in_org, get_store
from bankverse.db.models.tenant import (
    Account,
    AmlAlert,
    CardTransaction,
    Case,
    CaseEvent,
    Customer,
    FraudAlert,
    Report,
    Transfer,
)
from bankverse.services import audit, reports, security_events

router = APIRouter(tags=["cases", "reports"])
CASE_PERM = {"fraud": "fraud.read", "aml": "aml.read", "credit": "credit.read", "kyc": "documents.read"}


def _audit(p: Principal, request: Request, action: str, **kw: Any) -> None:
    audit.record(action, org_id=p.org_id, actor_user_id=p.user_id, actor_type=p.kind, actor_label=p.label,
                 ip=client_ip(request), **kw)


def _get_case(db: Session, p: Principal, case_id: uuid.UUID) -> Case:
    c = db.execute(select(Case).where(Case.id == case_id, Case.org_id == p.org_id)).scalar_one_or_none()
    if c is None or not p.can(CASE_PERM.get(c.case_type, "cases.read")):
        raise NotFound("Case not found")
    return c


@router.get("/cases")
def list_cases(p: Principal = Depends(require("cases.read")), db: Session = Depends(tenant_db),
               status: str | None = None, case_type: str | None = None, q: str | None = Query(default=None, max_length=100),
               page: int = Query(default=1, ge=1), page_size: int = Query(default=25, le=100)) -> dict[str, Any]:
    allowed = [t for t, perm in CASE_PERM.items() if p.can(perm)]
    query = select(Case, Customer).outerjoin(Customer, Customer.id == Case.customer_id).where(Case.case_type.in_(allowed))
    if status:
        query = query.where(Case.status.in_(status.split(",")))
    if case_type:
        query = query.where(Case.case_type == case_type)
    if q:
        query = query.where(Case.title.ilike(f"%{q}%") | Case.reference.ilike(f"%{q}%"))
    total = db.execute(select(func.count()).select_from(query.subquery())).scalar_one()
    rows = db.execute(query.order_by(Case.created_at.desc()).limit(page_size).offset((page - 1) * page_size)).all()
    return {"total": total, "items": [
        {"id": str(c.id), "reference": c.reference, "type": c.case_type, "title": c.title, "status": c.status,
         "priority": c.priority, "created_at": c.created_at, "decision": c.decision,
         "customer": cu.full_name if cu else None, "assigned_to": str(c.assigned_to) if c.assigned_to else None}
        for c, cu in rows]}


@router.get("/cases/{case_id}")
def get_case(case_id: uuid.UUID, p: Principal = Depends(require("cases.read")),
             db: Session = Depends(tenant_db)) -> dict[str, Any]:
    c = _get_case(db, p, case_id)
    events = db.execute(select(CaseEvent).where(CaseEvent.case_id == c.id).order_by(CaseEvent.created_at)).scalars().all()
    customer = db.get(Customer, c.customer_id) if c.customer_id else None
    fraud_alerts = db.execute(select(FraudAlert, CardTransaction).join(CardTransaction, CardTransaction.id == FraudAlert.transaction_id)
                              .where(FraudAlert.case_id == c.id)).all()
    aml_alerts = db.execute(select(AmlAlert, Account).join(Account, Account.id == AmlAlert.account_id)
                            .where(AmlAlert.case_id == c.id)).all()
    reps = db.execute(select(Report).where(Report.source_type == "case", Report.source_id == str(c.id))
                      .order_by(Report.created_at.desc())).scalars().all()
    return {
        "id": str(c.id), "reference": c.reference, "type": c.case_type, "title": c.title, "status": c.status,
        "priority": c.priority, "created_at": c.created_at, "summary": c.summary, "decision": c.decision,
        "decision_rationale": c.decision_rationale, "decided_at": c.decided_at,
        "customer": None if customer is None else {"id": str(customer.id), "name": customer.full_name,
                                                   "ref": customer.external_ref, "segment": customer.segment,
                                                   "risk_rating": customer.risk_rating, "kyc_status": customer.kyc_status,
                                                   "is_demo": customer.is_demo},
        "events": [{"id": str(e.id), "kind": e.kind, "actor": e.actor_label, "at": e.created_at, "payload": e.payload}
                   for e in events],
        "fraud_alerts": [{"id": str(a.id), "score": a.score, "band": a.risk_band, "status": a.status,
                          "amount": t.amount, "merchant": t.merchant_name, "ts": t.ts} for a, t in fraud_alerts],
        "aml_alerts": [{"id": str(a.id), "score": a.score, "band": a.risk_band, "status": a.status,
                        "typologies": a.typologies, "iban": acc.iban} for a, acc in aml_alerts],
        "reports": [{"id": str(r.id), "title": r.title, "created_at": r.created_at, "size": r.size_bytes} for r in reps],
    }


class NoteIn(BaseModel):
    text: str = Field(min_length=2, max_length=8000)
    kind: Literal["note", "evidence"] = "note"


@router.post("/cases/{case_id}/events")
def add_event(case_id: uuid.UUID, body: NoteIn, request: Request, p: Principal = Depends(require("cases.write")),
              db: Session = Depends(tenant_db)) -> dict[str, str]:
    c = _get_case(db, p, case_id)
    if c.status == "closed":
        raise Conflict("Case is closed")
    ev = CaseEvent(org_id=p.org_id, case_id=c.id, actor_id=p.user_id, actor_label=p.label, kind=body.kind,
                   payload={"text": body.text})
    db.add(ev)
    db.flush()
    _audit(p, request, f"case.{body.kind}_added", resource_type="case", resource_id=str(c.id))
    return {"id": str(ev.id)}


class StatusIn(BaseModel):
    status: Literal["open", "investigating", "pending_decision"]
    summary: str | None = Field(default=None, max_length=8000)


@router.patch("/cases/{case_id}")
def update_case(case_id: uuid.UUID, body: StatusIn, request: Request, p: Principal = Depends(require("cases.write")),
                db: Session = Depends(tenant_db)) -> dict[str, str]:
    c = _get_case(db, p, case_id)
    if c.status == "closed":
        raise Conflict("Case is closed")
    before = c.status
    c.status = body.status
    if body.summary is not None:
        c.summary = body.summary
    db.add(CaseEvent(org_id=p.org_id, case_id=c.id, actor_id=p.user_id, actor_label=p.label, kind="status_change",
                     payload={"from": before, "to": body.status}))
    _audit(p, request, "case.status_changed", resource_type="case", resource_id=str(c.id),
           details={"from": before, "to": body.status})
    return {"status": c.status}


DECISIONS = {
    "fraud": ["confirmed_fraud_card_blocked", "confirmed_fraud_customer_contacted", "false_positive", "inconclusive"],
    "aml": ["no_suspicion_closed", "enhanced_monitoring", "sar_filed_by_compliance", "relationship_exit_review"],
    "credit": ["approve", "decline", "request_information"],
    "kyc": ["documents_validated", "documents_rejected", "request_information"],
}


class DecisionIn(BaseModel):
    decision: str
    rationale: str = Field(min_length=20, max_length=8000)


@router.post("/cases/{case_id}/decision")
def decide(case_id: uuid.UUID, body: DecisionIn, request: Request, p: Principal = Depends(require("cases.decide")),
           db: Session = Depends(tenant_db)) -> dict[str, Any]:
    c = _get_case(db, p, case_id)
    if c.status == "closed":
        raise Conflict("Case already decided")
    if body.decision not in DECISIONS.get(c.case_type, []):
        raise ValidationFailed(f"Allowed decisions: {DECISIONS.get(c.case_type)}")
    if c.created_by == p.user_id and c.case_type in ("aml",):
        raise Forbidden("Four-eyes principle: the case creator cannot take the final AML decision", code="four_eyes")
    c.decision = body.decision
    c.decision_rationale = body.rationale
    c.decided_by = p.user_id
    c.decided_at = datetime.now(UTC)
    c.status = "closed"
    c.closed_at = datetime.now(UTC)
    db.add(CaseEvent(org_id=p.org_id, case_id=c.id, actor_id=p.user_id, actor_label=p.label, kind="decision",
                     payload={"decision": body.decision, "rationale": body.rationale}))
    # Propagate the human decision to linked alerts (feedback loop for model monitoring)
    if c.case_type == "fraud":
        label = "legitimate" if body.decision == "false_positive" else ("fraud" if body.decision.startswith("confirmed") else None)
        for a in db.execute(select(FraudAlert).where(FraudAlert.case_id == c.id)).scalars():
            if label and a.status not in ("closed_fraud", "closed_legitimate"):
                a.status = "closed_fraud" if label == "fraud" else "closed_legitimate"
                a.analyst_label = label
                a.resolved_at = datetime.now(UTC)
                a.resolution_note = f"Case {c.reference}: {body.decision}"
    if c.case_type == "aml":
        for a in db.execute(select(AmlAlert).where(AmlAlert.case_id == c.id)).scalars():
            a.status = "closed_reported" if body.decision == "sar_filed_by_compliance" else "closed_no_action"
            a.resolved_at = datetime.now(UTC)
    _audit(p, request, "case.decided", resource_type="case", resource_id=str(c.id),
           details={"decision": body.decision})
    return {"status": "closed", "decision": c.decision}


@router.post("/cases/{case_id}/report")
def generate_case_report(case_id: uuid.UUID, request: Request, p: Principal = Depends(require("reports.create")),
                         db: Session = Depends(tenant_db)) -> dict[str, Any]:
    c = _get_case(db, p, case_id)
    detail = get_case(case_id, p, db)
    sections: list[dict[str, Any]] = [
        {"heading": "1. Synthèse", "paragraphs": [
            f"Dossier <b>{c.reference}</b> — {c.title}", f"Type : {c.case_type.upper()} · Statut : {c.status} · Priorité : {c.priority}",
            f"Résumé de l'analyste : {c.summary or 'non renseigné'}"]},
    ]
    if detail["customer"]:
        cu = detail["customer"]
        sections.append({"heading": "2. Client", "table": [["Référence", "Nom", "Segment", "Risque", "KYC"],
                                                            [cu["ref"], cu["name"], cu["segment"], cu["risk_rating"], cu["kyc_status"]]]})
    if detail["fraud_alerts"]:
        rows = [["Alerte", "Score", "Niveau", "Montant", "Commerçant", "Date"]]
        for a in detail["fraud_alerts"]:
            rows.append([a["id"][:8], f"{a['score']:.3f}", a["band"], f"{a['amount']:.2f} €", a["merchant"],
                         a["ts"].strftime("%Y-%m-%d %H:%M")])
        sections.append({"heading": "3. Alertes fraude liées", "table": rows})
        for a in db.execute(select(FraudAlert).where(FraudAlert.case_id == c.id)).scalars():
            if a.reasons:
                sections.append({"heading": f"Facteurs explicatifs — alerte {str(a.id)[:8]}", "table": [
                    ["Facteur", "Valeur", "Contribution (log-odds)"],
                    *[[r["label"], r["value"], r["contribution"]] for r in a.reasons]]})
    if detail["aml_alerts"]:
        rows = [["Alerte", "Score", "Typologies", "IBAN"]]
        for a in detail["aml_alerts"]:
            rows.append([a["id"][:8], f"{a['score']:.3f}", ", ".join(a["typologies"]) or "—", a["iban"]])
        sections.append({"heading": "3. Alertes LCB-FT liées", "table": rows})
        for a in db.execute(select(AmlAlert).where(AmlAlert.case_id == c.id)).scalars():
            if a.evidence_transfer_ids:
                trs = db.execute(select(Transfer).where(Transfer.id.in_([uuid.UUID(x) for x in a.evidence_transfer_ids[:40]]))
                                 .order_by(Transfer.ts)).scalars().all()
                sections.append({"heading": f"Opérations justificatives — alerte {str(a.id)[:8]}", "table": [
                    ["Date", "Montant", "Canal", "Sens", "Contrepartie", "Référence"],
                    *[[t.ts.strftime("%Y-%m-%d %H:%M"), f"{t.amount:,.2f} €", t.channel, t.direction,
                       t.counterparty_name or (str(t.dst_account_id)[:8] if t.dst_account_id else "—"), t.reference]
                      for t in trs]]})
            if a.rule_hits:
                sections.append({"heading": "Règles déclenchées", "table": [["Règle", "Détail"], *[[h["rule"], h["detail"]] for h in a.rule_hits]]})
    sections.append({"heading": "4. Chronologie de l'investigation", "table": [
        ["Date", "Acteur", "Type", "Contenu"],
        *[[e["at"].strftime("%Y-%m-%d %H:%M"), e["actor"], e["kind"],
           (e["payload"].get("text") or e["payload"].get("decision") or ", ".join(f"{k}={v}" for k, v in e["payload"].items()))[:500]]
          for e in detail["events"]]]})
    if c.decision:
        sections.append({"heading": "5. Décision", "paragraphs": [f"Décision : <b>{c.decision}</b>",
                                                                  f"Motivation : {c.decision_rationale}"]})
    if c.case_type == "aml":
        sections.append({"heading": "Avertissement LCB-FT", "paragraphs": [
            "Une alerte LCB-FT n'est pas une preuve de blanchiment. La déclaration de soupçon éventuelle relève "
            "exclusivement du déclarant TRACFIN de l'établissement."]})
    pdf = reports.build_pdf(title=f"Rapport d'investigation {c.reference}", subtitle=c.title, org_name=p.org_name,
                            author=p.label, is_demo=p.org_kind == "demo", sections=sections)
    rep = reports.store_report(db, org_id=p.org_id, kind=f"{c.case_type}_case", title=f"Rapport {c.reference}", pdf=pdf,
                               created_by=p.actor_id, source_type="case", source_id=str(c.id))
    db.add(CaseEvent(org_id=p.org_id, case_id=c.id, actor_id=p.user_id, actor_label=p.label, kind="report",
                     payload={"report_id": str(rep.id), "sha256": rep.sha256}))
    _audit(p, request, "report.generated", resource_type="report", resource_id=str(rep.id),
           details={"case": c.reference, "sha256": rep.sha256})
    return {"report_id": str(rep.id), "sha256": rep.sha256, "size": rep.size_bytes}


@router.get("/reports")
def list_reports(p: Principal = Depends(require("reports.read")), db: Session = Depends(tenant_db),
                 kind: str | None = None) -> list[dict[str, Any]]:
    q = select(Report).order_by(Report.created_at.desc()).limit(200)
    if kind:
        q = q.where(Report.kind == kind)
    return [{"id": str(r.id), "kind": r.kind, "title": r.title, "format": r.fmt, "created_at": r.created_at,
             "size": r.size_bytes, "sha256": r.sha256, "source_type": r.source_type, "source_id": r.source_id}
            for r in db.execute(q).scalars()]


@router.get("/reports/{report_id}/download")
def download_report(report_id: uuid.UUID, request: Request, p: Principal = Depends(require("reports.read")),
                    db: Session = Depends(tenant_db)) -> Response:
    rep = db.execute(select(Report).where(Report.id == report_id, Report.org_id == p.org_id)).scalar_one_or_none()
    if rep is None:
        raise NotFound("Report not found")
    assert_key_in_org(rep.storage_key, p.org_id)
    data = get_store().get(rep.storage_key)
    _audit(p, request, "report.downloaded", resource_type="report", resource_id=str(rep.id))
    if security_events.count_recent(db=_control(), action="report.downloaded", user_id=p.user_id, minutes=10) > 20:
        security_events.emit("mass_export", severity="medium", org_id=p.org_id, user_id=p.user_id, ip=client_ip(request))
    return Response(content=data, media_type="application/pdf",
                    headers={"Content-Disposition": f'attachment; filename="{rep.title.replace(" ", "_")}.pdf"',
                             "X-Content-SHA256": rep.sha256})


def _control() -> Session:
    from bankverse.db.session import _ControlSession, app_engine

    return _ControlSession(bind=app_engine())


@router.get("/exports/{dataset}.csv")
def export_csv(dataset: Literal["fraud_alerts", "aml_alerts", "cases"], request: Request,
               p: Principal = Depends(require("reports.read")), db: Session = Depends(tenant_db)) -> Response:
    buf = io.StringIO()
    w = csv.writer(buf)
    if dataset == "fraud_alerts":
        if not p.can("fraud.read"):
            raise Forbidden("Missing permission: fraud.read")
        w.writerow(["alert_id", "created_at", "status", "band", "score", "amount", "merchant", "customer_ref",
                    "analyst_label", "model_version"])
        rows = db.execute(select(FraudAlert, CardTransaction, Customer).join(CardTransaction, CardTransaction.id == FraudAlert.transaction_id)
                          .join(Customer, Customer.id == FraudAlert.customer_id).order_by(FraudAlert.created_at.desc()).limit(50000)).all()
        for a, t, c in rows:
            w.writerow([a.id, a.created_at.isoformat(), a.status, a.risk_band, round(a.score, 5), t.amount, t.merchant_name,
                        c.external_ref, a.analyst_label or "", a.model_version])
    elif dataset == "aml_alerts":
        if not p.can("aml.read"):
            raise Forbidden("Missing permission: aml.read")
        w.writerow(["alert_id", "created_at", "status", "band", "score", "typologies", "account_iban", "customer_ref"])
        rows = db.execute(select(AmlAlert, Account, Customer).join(Account, Account.id == AmlAlert.account_id)
                          .join(Customer, Customer.id == AmlAlert.customer_id).order_by(AmlAlert.score.desc()).limit(50000)).all()
        for a, acc, c in rows:
            w.writerow([a.id, a.created_at.isoformat(), a.status, a.risk_band, round(a.score, 5), "|".join(a.typologies),
                        acc.iban, c.external_ref])
    else:
        w.writerow(["case_id", "reference", "type", "status", "priority", "created_at", "decision"])
        for c in db.execute(select(Case).order_by(Case.created_at.desc()).limit(50000)).scalars():
            if p.can(CASE_PERM.get(c.case_type, "cases.read")):
                w.writerow([c.id, c.reference, c.case_type, c.status, c.priority, c.created_at.isoformat(), c.decision or ""])
    _audit(p, request, "export.csv", resource_type="export", resource_id=dataset)
    return Response(content=buf.getvalue(), media_type="text/csv",
                    headers={"Content-Disposition": f'attachment; filename="{dataset}.csv"'})
