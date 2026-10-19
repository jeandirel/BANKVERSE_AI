"""Typed agent tools. Each tool declares the permission it needs (checked against the calling principal) and
whether it only reads (`read`) or changes something (`action`). Action tools are never executed by an agent:
they are proposed and run only after an explicit human approval, with the approver's own permissions.

No tool can move money, decide a credit application, block a card, or file a regulatory report."""

from __future__ import annotations

import statistics
import time
import uuid
from collections import Counter
from collections.abc import Callable
from dataclasses import dataclass, field
from typing import Any, Literal

from fastapi import Request
from pydantic import BaseModel, Field
from sqlalchemy import func, select
from sqlalchemy.orm import Session

from bankverse.auth.deps import Principal
from bankverse.core.errors import Forbidden, NotFound
from bankverse.db.models.tenant import (
    AmlAlert,
    Case,
    CaseEvent,
    CreditApplication,
    Customer,
    Document,
    DocumentCheck,
    FraudAlert,
    Incident,
)
from bankverse.modules.copilot import guardrails


@dataclass
class ToolContext:
    db: Session
    principal: Principal
    request: Request | None
    log: Callable[[str, dict[str, Any], dict[str, Any] | None, str, int, str | None], None] = lambda *a: None
    deadline: float = field(default_factory=lambda: time.monotonic() + 60)


@dataclass
class Tool:
    name: str
    kind: Literal["read", "action"]
    permission: str
    description: str
    input_model: type[BaseModel]
    fn: Callable[[ToolContext, Any], dict[str, Any]]
    label_fr: str
    label_en: str


TOOLS: dict[str, Tool] = {}


def tool(name: str, kind: Literal["read", "action"], permission: str, description: str, input_model: type[BaseModel], label_fr: str,
         label_en: str) -> Callable[[Callable[[ToolContext, Any], dict[str, Any]]], Callable[[ToolContext, Any], dict[str, Any]]]:
    def deco(fn: Callable[[ToolContext, Any], dict[str, Any]]) -> Callable[[ToolContext, Any], dict[str, Any]]:
        TOOLS[name] = Tool(name, kind, permission, description, input_model, fn, label_fr, label_en)
        return fn

    return deco


def call(ctx: ToolContext, name: str, raw_input: dict[str, Any]) -> dict[str, Any]:
    t = TOOLS[name]
    if not ctx.principal.can(t.permission):
        raise Forbidden(f"Tool {name} requires permission {t.permission}", code="tool_forbidden")
    if time.monotonic() > ctx.deadline:
        raise TimeoutError("agent time budget exhausted")
    args = t.input_model.model_validate(raw_input)
    t0 = time.perf_counter()
    try:
        out = t.fn(ctx, args)
    except Exception as exc:
        ctx.log(name, args.model_dump(mode="json"), None, "error", int((time.perf_counter() - t0) * 1000), f"{type(exc).__name__}: {exc}")
        raise
    note = None
    if out.pop("_suspicious", False):
        note = "Contenu non fiable détecté (instructions dans les données) : exclu de la synthèse."
    ctx.log(name, args.model_dump(mode="json"), out, "ok", int((time.perf_counter() - t0) * 1000), note)
    return out


# ------------------------------------------------------------------------------------------------
# Inputs
# ------------------------------------------------------------------------------------------------
class IdIn(BaseModel):
    id: uuid.UUID


class NetworkIn(BaseModel):
    account_id: uuid.UUID
    hops: int = Field(default=2, ge=1, le=3)


class ForecastIn(BaseModel):
    series: str = Field(max_length=40, pattern=r"^[a-z0-9_]+$")
    horizon: int = Field(default=12, ge=1, le=24)


class SearchIn(BaseModel):
    question: str = Field(min_length=3, max_length=400)
    scope: Literal["public", "private", "all"] = "private"


class NoIn(BaseModel):
    pass


class CreateCaseIn(BaseModel):
    kind: Literal["fraud", "aml", "credit", "kyc"]
    entity_id: uuid.UUID
    title: str | None = Field(default=None, max_length=300)


class NoteIn(BaseModel):
    case_id: uuid.UUID
    text: str = Field(min_length=2, max_length=8000)


class EscalateIn(BaseModel):
    alert_id: uuid.UUID
    note: str = Field(min_length=3, max_length=4000)


class IncidentIn(BaseModel):
    model_name: str = Field(max_length=120)
    severity: Literal["low", "medium", "high", "critical"]
    title: str = Field(max_length=300)
    description: str = Field(max_length=4000)


# ------------------------------------------------------------------------------------------------
# Read tools
# ------------------------------------------------------------------------------------------------
@tool("fraud_alert", "read", "fraud.read", "Card-fraud alert with model reasons, transaction and 30-day card history", IdIn,
      "Lire l'alerte de fraude", "Read the fraud alert")
def fraud_alert(ctx: ToolContext, a: IdIn) -> dict[str, Any]:
    from bankverse.api.v1 import fraud as api

    d = api.get_alert(a.id, ctx.principal, ctx.db)
    hist = [h for h in d["history"] if not h["is_current"]]
    amounts = [h["amount"] for h in hist]
    return {"alert_id": d["id"], "score": d["score"], "risk_band": d["risk_band"], "status": d["status"], "expected_loss": d["expected_loss"],
            "case_id": d["case_id"], "reasons": [r["label"] for r in d["reasons"][:5]], "transaction": d["transaction"],
            "customer": {**d["customer"], **d["customer_profile"]},
            "card_history": {"transactions": len(hist), "median_amount": round(statistics.median(amounts), 2) if amounts else None,
                             "max_amount": max(amounts) if amounts else None, "distinct_cities": len({h["city"] for h in hist}),
                             "with_errors": sum(1 for h in hist if h["errors"])},
            "other_alerts_on_card": len(d["other_alerts_on_card"]), "model_version": d["model_version"]}


@tool("aml_alert", "read", "aml.read", "AML alert: typologies, rule hits, model scores, evidence transfers", IdIn,
      "Lire l'alerte LCB-FT", "Read the AML alert")
def aml_alert(ctx: ToolContext, a: IdIn) -> dict[str, Any]:
    from bankverse.api.v1 import aml as api

    d = api.get_alert(a.id, ctx.principal, ctx.db)
    ev = d["evidence"]
    countries = Counter(e["counterparty_country"] for e in ev if e.get("counterparty_country"))
    return {"alert_id": d["id"], "score": d["score"], "risk_band": d["risk_band"], "status": d["status"], "typologies": d["typologies"],
            "rule_hits": d["rule_hits"], "model_scores": d["model_scores"], "case_id": d["case_id"], "account": d["account"],
            "customer": d["customer"], "evidence": {"transfers": len(ev), "total_amount": round(sum(e["amount"] for e in ev), 2),
                                                     "first": ev[0]["ts"] if ev else None, "last": ev[-1]["ts"] if ev else None,
                                                     "counterparty_countries": dict(countries)}}


@tool("aml_network", "read", "aml.read", "Counterparty network around an account (2 hops)", NetworkIn,
      "Analyser le réseau de contreparties", "Analyse the counterparty network")
def aml_network(ctx: ToolContext, a: NetworkIn) -> dict[str, Any]:
    from bankverse.modules.aml.service import ego_network

    g = ego_network(ctx.db, a.account_id, hops=a.hops, max_nodes=60)
    focus = str(a.account_id)
    inflow = sum(e["total"] for e in g["edges"] if e["target"] == focus)
    outflow = sum(e["total"] for e in g["edges"] if e["source"] == focus)
    flagged = [n for n in g["nodes"] if n.get("alert_id") and n["id"] != focus]
    top = sorted(g["edges"], key=lambda e: -e["total"])[:5]
    labels = {n["id"]: n["label"] for n in g["nodes"]}
    return {"nodes": len(g["nodes"]), "edges": len(g["edges"]), "external_nodes": sum(1 for n in g["nodes"] if n["kind"] == "external"),
            "inflow": round(inflow, 2), "outflow": round(outflow, 2), "other_alerted_accounts": len(flagged),
            "suspicious_edges": sum(1 for e in g["edges"] if e["suspicious"]),
            "top_flows": [{"from": labels.get(e["source"], e["source"][:8]), "to": labels.get(e["target"], e["target"][:8]), "total": e["total"],
                           "count": e["count"]} for e in top]}


@tool("credit_application", "read", "credit.read", "Credit application with PD, grade and explanatory factors", IdIn,
      "Lire la demande de crédit", "Read the credit application")
def credit_application(ctx: ToolContext, a: IdIn) -> dict[str, Any]:
    from bankverse.api.v1 import credit as api

    d = api.get_application(a.id, ctx.principal, ctx.db)
    return {"application_id": d["id"], "reference": d["reference"], "status": d["status"], "requested_limit": d["requested_limit"], "pd": d["pd"],
            "grade": d["grade"], "factors": [{"label": f["label"], "contribution": f["contribution"]} for f in (d["explanation"] or [])[:5]],
            "decision": d["decision"], "customer": d["customer"], "model_version": d["model_version"]}


@tool("customer_profile", "read", "customer.read", "Customer 360: accounts, cards, segment, messages, alerts", IdIn,
      "Consulter la vue client 360", "Open the customer 360 view")
def customer_profile(ctx: ToolContext, a: IdIn) -> dict[str, Any]:
    from bankverse.api.v1 import customers as api

    req = ctx.request
    d = api.customer_360(a.id, req, ctx.principal, ctx.db)  # type: ignore[arg-type]
    msgs = d["messages"]
    suspicious = any(guardrails.evidence_is_suspicious(m["text"]) for m in msgs)
    return {"customer_id": d["id"], "name": d["name"], "ref": d["ref"], "type": d["type"], "segment": d["segment"], "risk_rating": d["risk_rating"],
            "kyc_status": d["kyc_status"], "pep": d["pep"], "occupation": d["occupation"], "annual_income": d["annual_income"],
            "customer_since": d["onboarded_at"], "accounts": len(d["accounts"]), "total_balance": round(sum(x["balance"] for x in d["accounts"]), 2),
            "cards": len(d["cards"]), "behaviour_segment": (d["behaviour_segment"] or {}).get("label"),
            "messages": len(msgs), "complaints": sum(1 for m in msgs if m["complaint"]),
            "message_topics": dict(Counter(m["group"] for m in msgs if m["group"])), "fraud_alerts": d.get("fraud_alerts"),
            "aml_alerts": d.get("aml_alerts"), "credit_applications": len(d.get("credit_applications", [])), "is_demo": d["is_demo"],
            "_suspicious": suspicious}


@tool("customer_documents", "read", "documents.read", "Supporting documents of a customer with failed controls", IdIn,
      "Contrôler les justificatifs du client", "Check the customer's documents")
def customer_documents(ctx: ToolContext, a: IdIn) -> dict[str, Any]:
    docs = ctx.db.execute(select(Document).where(Document.customer_id == a.id, Document.doc_type != "kb")
                          .order_by(Document.created_at.desc()).limit(30)).scalars().all()
    fails = {}
    if docs:
        for did, name, detail in ctx.db.execute(select(DocumentCheck.document_id, DocumentCheck.check_name, DocumentCheck.detail)
                                                .where(DocumentCheck.document_id.in_([d.id for d in docs]), DocumentCheck.status == "fail")):
            fails.setdefault(did, []).append({"check": name, "detail": detail})
    return {"documents": [{"id": str(d.id), "type": d.doc_type, "status": d.status, "filename": d.filename, "created_at": d.created_at,
                           "failed_checks": fails.get(d.id, [])} for d in docs],
            "count": len(docs), "with_failures": sum(1 for d in docs if d.id in fails),
            "validated": sum(1 for d in docs if d.status == "validated")}


@tool("forecast", "read", "forecast.read", "Statistical forecast of a macro-financial series with uncertainty intervals", ForecastIn,
      "Calculer une prévision", "Compute a forecast")
def forecast(ctx: ToolContext, a: ForecastIn) -> dict[str, Any]:
    import pandas as pd

    from bankverse.modules.forecasting import engine
    from bankverse.modules.forecasting.train import load_series

    s = load_series(a.series).loc["1999-01-01":]
    y = engine.transform(a.series, s.to_numpy(dtype=float))
    months = s.index.month.to_numpy()
    out = {}
    for m in ("naive", "ets", "arima"):
        try:
            f = engine.run_model(m, y, a.horizon, months)
        except Exception as exc:  # model failure is reported, not hidden
            out[m] = {"status": "failed", "error": type(exc).__name__}
            continue
        mean = engine.inverse(a.series, f.mean)
        lo, hi = engine.inverse(a.series, f.lo80), engine.inverse(a.series, f.hi80)
        out[m] = {"status": "ok", "end_mean": round(float(mean[-1]), 4),  # type: ignore[index]
                  "end_lo80": None if lo is None else round(float(lo[-1]), 4), "end_hi80": None if hi is None else round(float(hi[-1]), 4)}
    end = pd.date_range(s.index[-1] + pd.offsets.MonthBegin(1), periods=a.horizon, freq="MS")[-1]
    return {"series": a.series, "last_date": str(s.index[-1].date()), "last_value": round(float(s.iloc[-1]), 4), "horizon_end": str(end.date()),
            "forecasts": out}


@tool("knowledge_search", "read", "copilot.use", "Search the bank's procedures (private) or public reports, with citations", SearchIn,
      "Rechercher dans les procédures", "Search procedures")
def knowledge_search(ctx: ToolContext, a: SearchIn) -> dict[str, Any]:
    from bankverse.modules.copilot import service

    ret, tag = service.retriever()
    facts = service.search_private(ctx.db, ret, a.question) if a.scope != "public" else []
    if a.scope != "private":
        facts += service.search_public(ret, a.question)
    ev = service.rank_evidence(ret, a.question, facts, k=3)
    clean = [e for e in ev if not guardrails.evidence_is_suspicious(e["content"])]
    return {"retriever": tag, "evidence": [{"title": e["title"], "content": e["content"][:700], "scope": e["scope"], "chunk_id": e["id"]}
                                           for e in clean], "_suspicious": len(clean) != len(ev)}


@tool("case_detail", "read", "cases.read", "Investigation case with events and linked alerts", IdIn, "Lire le dossier", "Read the case")
def case_detail(ctx: ToolContext, a: IdIn) -> dict[str, Any]:
    from bankverse.api.v1 import cases as api

    d = api.get_case(a.id, ctx.principal, ctx.db)
    return {"case_id": d["id"], "reference": d["reference"], "type": d["type"], "status": d["status"], "priority": d["priority"],
            "title": d["title"], "decision": d.get("decision"), "events": len(d.get("events", [])),
            "fraud_alerts": len(d.get("fraud_alerts", [])), "aml_alerts": len(d.get("aml_alerts", [])), "reports": len(d.get("reports", [])),
            "customer": d.get("customer")}


@tool("models_status", "read", "models.read", "Production models with versions, test metrics and live drift (PSI)", NoIn,
      "Vérifier l'état des modèles", "Check model status")
def models_status(ctx: ToolContext, a: NoIn) -> dict[str, Any]:
    from bankverse.modules.governance import monitoring

    return {"models": monitoring.models_overview(ctx.db)}


# ------------------------------------------------------------------------------------------------
# Action tools (human approval required)
# ------------------------------------------------------------------------------------------------
@tool("create_case", "action", "cases.write", "Open an investigation case linked to an alert, application or customer", CreateCaseIn,
      "Ouvrir un dossier d'investigation", "Open an investigation case")
def create_case(ctx: ToolContext, a: CreateCaseIn) -> dict[str, Any]:
    from bankverse.api.v1 import aml as aml_api
    from bankverse.api.v1 import credit as credit_api
    from bankverse.api.v1 import fraud as fraud_api
    from bankverse.services.cases import new_case_reference

    p, db, req = ctx.principal, ctx.db, ctx.request
    if a.kind == "fraud":
        return fraud_api.open_case(a.entity_id, fraud_api.OpenCaseIn(title=a.title), req, p, db)  # type: ignore[arg-type]
    if a.kind == "aml":
        return aml_api.open_case(a.entity_id, req, p, db)  # type: ignore[arg-type]
    if a.kind == "credit":
        return credit_api.credit_case(a.entity_id, req, p, db)  # type: ignore[arg-type]
    c = db.execute(select(Customer).where(Customer.id == a.entity_id, Customer.org_id == p.org_id)).scalar_one_or_none()
    if c is None:
        raise NotFound("Customer not found")
    case_obj = Case(org_id=p.org_id, reference=new_case_reference(db, "KY"), case_type="kyc", title=a.title or f"Revue KYC — {c.full_name}",
                    status="investigating", priority="medium", customer_id=c.id, created_by=p.actor_id, assigned_to=p.user_id)
    db.add(case_obj)
    db.flush()
    return {"case_id": str(case_obj.id), "reference": case_obj.reference}


@tool("add_case_note", "action", "cases.write", "Add the agent's analysis as a note on a case", NoteIn,
      "Ajouter une note au dossier", "Add a note to the case")
def add_case_note(ctx: ToolContext, a: NoteIn) -> dict[str, Any]:
    c = ctx.db.execute(select(Case).where(Case.id == a.case_id, Case.org_id == ctx.principal.org_id)).scalar_one_or_none()
    if c is None:
        raise NotFound("Case not found")
    ev = CaseEvent(org_id=c.org_id, case_id=c.id, actor_id=ctx.principal.user_id, actor_label=f"{ctx.principal.label} (via agent)", kind="note",
                   payload={"text": a.text, "source": "agent"})
    ctx.db.add(ev)
    ctx.db.flush()
    return {"case_id": str(c.id), "event_id": str(ev.id)}


@tool("generate_case_report", "action", "reports.create", "Generate the PDF investigation report of a case", IdIn,
      "Générer le rapport PDF du dossier", "Generate the case PDF report")
def generate_case_report(ctx: ToolContext, a: IdIn) -> dict[str, Any]:
    from bankverse.api.v1 import cases as api

    return api.generate_case_report(a.id, ctx.request, ctx.principal, ctx.db)  # type: ignore[arg-type]


@tool("escalate_fraud_alert", "action", "fraud.triage", "Escalate a fraud alert to the risk manager with a note", EscalateIn,
      "Escalader l'alerte de fraude", "Escalate the fraud alert")
def escalate_fraud_alert(ctx: ToolContext, a: EscalateIn) -> dict[str, Any]:
    from bankverse.api.v1 import fraud as api

    r = api.update_alert(a.alert_id, api.AlertUpdateIn(status="escalated", note=a.note), ctx.request, ctx.principal, ctx.db)  # type: ignore[arg-type]
    return {"alert_id": str(a.alert_id), "status": r.get("status", "escalated")}


@tool("declare_model_incident", "action", "governance.manage", "Declare a model-risk incident (drift, errors)", IncidentIn,
      "Déclarer un incident modèle", "Declare a model incident")
def declare_model_incident(ctx: ToolContext, a: IncidentIn) -> dict[str, Any]:
    inc = Incident(org_id=ctx.principal.org_id, title=a.title, category="model_drift", severity=a.severity, model_name=a.model_name,
                   description=a.description, opened_by=f"{ctx.principal.label} (via agent)", detected_automatically=True)
    ctx.db.add(inc)
    ctx.db.flush()
    return {"incident_id": str(inc.id)}


def resolve_entity(db: Session, entity_id: uuid.UUID) -> str | None:
    """Which kind of object a bare identifier refers to (within the tenant, RLS-scoped)."""
    for model, kind in ((FraudAlert, "fraud_alert"), (AmlAlert, "aml_alert"), (CreditApplication, "credit_application"),
                        (Customer, "customer"), (Case, "case")):
        if db.execute(select(func.count()).select_from(model).where(model.id == entity_id)).scalar_one():
            return kind
    return None
