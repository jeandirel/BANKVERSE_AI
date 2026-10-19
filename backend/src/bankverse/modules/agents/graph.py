"""Agent orchestration with LangGraph.

graph:  guard ──blocked──▶ END
          │
        route ──needs input──▶ END
          │
        gather (agent plan: typed read-only tools, step & time budgets)
          │
        analyze (deterministic narrative; grounded LLM rewrite only if a provider is configured)
          │
        propose (action tools → pending human approvals, never executed here)
          │
        finalize ──▶ END

The planner is deterministic (a fixed, auditable plan per agent). Every tool call is logged as an
AgentStep; untrusted text found in tool outputs (customer messages, documents) is flagged and kept out of
narratives and LLM prompts."""

from __future__ import annotations

import json
import re
import time
import uuid
from collections.abc import Callable
from dataclasses import dataclass
from typing import Any, TypedDict

from langgraph.graph import END, START, StateGraph

from bankverse.core.config import get_settings
from bankverse.core.errors import AppError
from bankverse.modules.agents import tools as T
from bankverse.modules.copilot import guardrails, llm


class AgentState(TypedDict, total=False):
    request: str
    agent: str | None
    entity_type: str | None
    entity_id: str | None
    params: dict[str, Any]
    locale: str
    findings: dict[str, Any]
    skipped: list[dict[str, str]]
    narrative: list[str]
    sources: list[dict[str, Any]]
    proposals: list[dict[str, Any]]
    status: str
    message: str | None
    planner: str


@dataclass
class AgentSpec:
    key: str
    label_fr: str
    label_en: str
    description_fr: str
    description_en: str
    entity_type: str | None
    permission: str
    plan: Callable[[T.ToolContext, AgentState], None]
    narrate: Callable[[AgentState], list[str]]
    propose: Callable[[AgentState], list[dict[str, Any]]]


def _L(state: AgentState, fr: str, en: str) -> str:
    return en if state.get("locale") == "en" else fr


def _try(ctx: T.ToolContext, state: AgentState, key: str, tool: str, args: dict[str, Any]) -> dict[str, Any] | None:
    """Optional evidence: a missing permission or an unavailable component is recorded, not fatal."""
    t = T.TOOLS[tool]
    if not ctx.principal.can(t.permission):
        state["skipped"].append({"tool": tool, "reason": f"permission {t.permission}"})
        return None
    try:
        out = T.call(ctx, tool, args)
    except AppError as exc:
        state["skipped"].append({"tool": tool, "reason": exc.code})
        return None
    state["findings"][key] = out
    return out


def _must(ctx: T.ToolContext, state: AgentState, key: str, tool: str, args: dict[str, Any]) -> dict[str, Any]:
    out = T.call(ctx, tool, args)
    state["findings"][key] = out
    return out


def _eur(v: float | None) -> str:
    return "—" if v is None else f"{v:,.2f} €".replace(",", " ").replace(".", ",")


def _kb_lines(state: AgentState) -> list[str]:
    kb = state["findings"].get("procedure")
    if not kb or not kb.get("evidence"):
        return [_L(state, "Aucune procédure interne pertinente n'a été retrouvée.", "No relevant internal procedure was found.")]
    out = []
    for e in kb["evidence"][:2]:
        n = len(state["sources"]) + 1
        state["sources"].append({"n": n, "title": e["title"], "scope": e["scope"], "chunk_id": e["chunk_id"], "excerpt": e["content"][:400]})
        first = re.split(r"(?<=[.!?])\s", e["content"].strip(), maxsplit=1)[0][:300]
        out.append(_L(state, f"Procédure [{n}] « {e['title']} » : {first}", f"Procedure [{n}] “{e['title']}”: {first}"))
    return out


# ------------------------------------------------------------------------------------------------
# Agents
# ------------------------------------------------------------------------------------------------
def plan_fraud(ctx: T.ToolContext, s: AgentState) -> None:
    a = _must(ctx, s, "alert", "fraud_alert", {"id": s["entity_id"]})
    _try(ctx, s, "customer", "customer_profile", {"id": a["customer"]["id"]})
    _try(ctx, s, "procedure", "knowledge_search", {"question": "procédure alertes fraude carte délais escalade blocage préjudice", "scope": "private"})


def narrate_fraud(s: AgentState) -> list[str]:
    a, c = s["findings"]["alert"], s["findings"].get("customer")
    t, h = a["transaction"], a["card_history"]
    out = [_L(s, f"Alerte {a['risk_band']} (score {a['score']:.2f}) sur un paiement de {_eur(t['amount'])} chez {t['merchant']} "
                 f"({t['merchant_city']}, MCC {t['mcc']}, canal {t['channel']}).",
              f"{a['risk_band'].capitalize()} alert (score {a['score']:.2f}) on a {_eur(t['amount'])} payment at {t['merchant']} "
              f"({t['merchant_city']}, MCC {t['mcc']}, channel {t['channel']}).")]
    if a["reasons"]:
        out.append(_L(s, "Facteurs principaux du modèle : ", "Main model factors: ") + " ; ".join(a["reasons"][:4]) + ".")
    if h["transactions"]:
        ratio = t["amount"] / h["median_amount"] if h["median_amount"] else None
        out.append(_L(s, f"Carte sur 30 jours : {h['transactions']} opérations, montant médian {_eur(h['median_amount'])}, maximum {_eur(h['max_amount'])}, "
                         f"{h['distinct_cities']} villes, {h['with_errors']} avec erreurs techniques"
                         + (f" ; ce paiement vaut {ratio:.1f}× le montant médian." if ratio else "."),
                      f"Card over 30 days: {h['transactions']} payments, median {_eur(h['median_amount'])}, max {_eur(h['max_amount'])}, "
                      f"{h['distinct_cities']} cities, {h['with_errors']} with technical errors" + (f"; this payment is {ratio:.1f}× the median." if ratio else ".")))
    else:
        out.append(_L(s, "Aucun historique de carte sur 30 jours (carte récente ou inactive).", "No card history over 30 days (new or dormant card)."))
    if c:
        out.append(_L(s, f"Client {c['segment']} ({c['ref']}), notation {c['risk_rating']}, KYC {c['kyc_status']}, {c['fraud_alerts'] or 0} alerte(s) fraude au total, "
                         f"{c['complaints']} réclamation(s) récente(s).",
                      f"{c['segment']} customer ({c['ref']}), rating {c['risk_rating']}, KYC {c['kyc_status']}, {c['fraud_alerts'] or 0} fraud alert(s) in total, "
                      f"{c['complaints']} recent complaint(s)."))
    out += _kb_lines(s)
    out.append(_L(s, "Ces éléments sont des indices : la qualification (fraude ou opération légitime) et tout blocage de carte restent des décisions humaines.",
                  "These are indicators: qualifying the alert (fraud or legitimate) and any card block remain human decisions."))
    return out


def propose_fraud(s: AgentState) -> list[dict[str, Any]]:
    a = s["findings"]["alert"]
    props = []
    if a["status"] not in ("closed_fraud", "closed_legitimate"):
        if a["case_id"] is None and a["risk_band"] in ("critical", "high"):
            props.append({"tool": "create_case", "input": {"kind": "fraud", "entity_id": a["alert_id"]},
                          "rationale": _L(s, "Alerte de niveau élevé sans dossier : ouvrir un dossier d'investigation.", "High-band alert without a case: open an investigation case.")})
        if a["status"] != "escalated" and (a["risk_band"] == "critical" or (a["expected_loss"] or 0) > 5000):
            props.append({"tool": "escalate_fraud_alert", "input": {"alert_id": a["alert_id"], "note": " ".join(s["narrative"][:3])[:3900]},
                          "rationale": _L(s, "Niveau critique ou préjudice potentiel > 5 000 € : escalade prévue par la procédure.",
                                          "Critical band or potential loss > €5,000: escalation required by the procedure.")})
    return props


def plan_aml(ctx: T.ToolContext, s: AgentState) -> None:
    a = _must(ctx, s, "alert", "aml_alert", {"id": s["entity_id"]})
    _try(ctx, s, "network", "aml_network", {"account_id": a["account"]["id"], "hops": 2})
    _try(ctx, s, "customer", "customer_profile", {"id": a["customer"]["id"]})
    _try(ctx, s, "documents", "customer_documents", {"id": a["customer"]["id"]})
    _try(ctx, s, "procedure", "knowledge_search", {"question": "revue alertes LCB-FT blanchiment quatre yeux déclaration délais", "scope": "private"})


def narrate_aml(s: AgentState) -> list[str]:
    a, n, c, d = (s["findings"].get(k) for k in ("alert", "network", "customer", "documents"))
    assert a is not None
    ev = a["evidence"]
    rules = ", ".join(sorted({h.get("rule", h.get("id", "?")) if isinstance(h, dict) else str(h) for h in (a["rule_hits"] or [])})) or "—"
    out = [_L(s, f"Alerte LCB-FT {a['risk_band']} (score {a['score']:.2f}) sur le compte {a['account']['iban']} : typologies {', '.join(a['typologies']) or '—'} ; règles {rules}.",
              f"{a['risk_band'].capitalize()} AML alert (score {a['score']:.2f}) on account {a['account']['iban']}: typologies {', '.join(a['typologies']) or '—'}; rules {rules}."),
           _L(s, f"Éléments : {ev['transfers']} virements pour {_eur(ev['total_amount'])}"
                 + (f", pays des contreparties : {', '.join(f'{k} ({v})' for k, v in ev['counterparty_countries'].items())}." if ev["counterparty_countries"] else "."),
              f"Evidence: {ev['transfers']} transfers totalling {_eur(ev['total_amount'])}"
                 + (f", counterparty countries: {', '.join(f'{k} ({v})' for k, v in ev['counterparty_countries'].items())}." if ev["counterparty_countries"] else "."))]
    if n:
        out.append(_L(s, f"Réseau à 2 niveaux : {n['nodes']} nœuds dont {n['external_nodes']} externes, entrées {_eur(n['inflow'])}, sorties {_eur(n['outflow'])}, "
                         f"{n['other_alerted_accounts']} autre(s) compte(s) en alerte, {n['suspicious_edges']} flux marqué(s).",
                      f"2-hop network: {n['nodes']} nodes ({n['external_nodes']} external), inflow {_eur(n['inflow'])}, outflow {_eur(n['outflow'])}, "
                      f"{n['other_alerted_accounts']} other alerted account(s), {n['suspicious_edges']} flagged flow(s)."))
    cust = a["customer"]
    vol = a["account"]["volume"]
    if cust.get("annual_income"):
        out.append(_L(s, f"Client {cust['name']} ({cust['occupation'] or 'profession non renseignée'}), revenus déclarés {_eur(cust['annual_income'])} / an ; "
                         f"volume du compte {_eur(vol)} ({vol / cust['annual_income']:.1f}× les revenus annuels).",
                      f"Customer {cust['name']} ({cust['occupation'] or 'occupation not provided'}), declared income {_eur(cust['annual_income'])} / year; "
                      f"account volume {_eur(vol)} ({vol / cust['annual_income']:.1f}× annual income)."))
    if d is not None:
        out.append(_L(s, f"Justificatifs : {d['count']} document(s), {d['validated']} validé(s), {d['with_failures']} avec contrôle en échec.",
                      f"Documents: {d['count']} on file, {d['validated']} validated, {d['with_failures']} with failed controls."))
    if c and c.get("pep"):
        out.append(_L(s, "Le client est signalé personne politiquement exposée (PPE).", "The customer is flagged as a politically exposed person (PEP)."))
    out += _kb_lines(s)
    out.append(_L(s, "Une alerte n'est pas une preuve de blanchiment. La clôture exige un second regard et toute déclaration relève du seul responsable conformité.",
                  "An alert is not proof of money laundering. Closing requires a second reviewer and any report is solely the compliance officer's decision."))
    return out


def propose_aml(s: AgentState) -> list[dict[str, Any]]:
    a = s["findings"]["alert"]
    if a["case_id"] is None and a["status"] != "closed_no_action":
        return [{"tool": "create_case", "input": {"kind": "aml", "entity_id": a["alert_id"]},
                 "rationale": _L(s, "Ouvrir un dossier LCB-FT pour instruire l'alerte (preuves et réseau joints).",
                                 "Open an AML case to investigate the alert (evidence and network attached).")}]
    return []


def plan_credit(ctx: T.ToolContext, s: AgentState) -> None:
    a = _must(ctx, s, "application", "credit_application", {"id": s["entity_id"]})
    if a["customer"]:
        _try(ctx, s, "customer", "customer_profile", {"id": a["customer"]["id"]})
        _try(ctx, s, "documents", "customer_documents", {"id": a["customer"]["id"]})
    _try(ctx, s, "procedure", "knowledge_search", {"question": "politique crédit grades justificatifs taux d'effort second analyste", "scope": "private"})


def narrate_credit(s: AgentState) -> list[str]:
    a, d = s["findings"]["application"], s["findings"].get("documents")
    out = [_L(s, f"Demande {a['reference']} : plafond demandé {_eur(a['requested_limit'])}, probabilité de défaut estimée {a['pd'] * 100:.1f} % (grade {a['grade']}).",
              f"Application {a['reference']}: requested limit {_eur(a['requested_limit'])}, estimated probability of default {a['pd'] * 100:.1f}% (grade {a['grade']}).")
           if a["pd"] is not None else _L(s, f"Demande {a['reference']} non encore scorée.", f"Application {a['reference']} not scored yet.")]
    if a["factors"]:
        out.append(_L(s, "Facteurs : ", "Factors: ") + " ; ".join(f"{f['label']} ({f['contribution']:+.2f})" for f in a["factors"][:4]) + ".")
    cu = a["customer"]
    if cu and cu.get("annual_income"):
        out.append(_L(s, f"Revenus déclarés {_eur(cu['annual_income'])} / an ; le plafond représente {a['requested_limit'] / cu['annual_income'] * 100:.0f} % des revenus annuels.",
                      f"Declared income {_eur(cu['annual_income'])} / year; the limit is {a['requested_limit'] / cu['annual_income'] * 100:.0f}% of annual income."))
    if d is not None:
        out.append(_L(s, f"Justificatifs : {d['count']} document(s), {d['validated']} validé(s), {d['with_failures']} avec contrôle en échec.",
                      f"Documents: {d['count']} on file, {d['validated']} validated, {d['with_failures']} with failed controls."))
    out += _kb_lines(s)
    out.append(_L(s, "L'agent ne prend aucune décision d'octroi ou de refus : la décision motivée appartient à l'analyste crédit.",
                  "The agent makes no approval or refusal: the reasoned decision belongs to the credit analyst."))
    return out


def propose_credit(s: AgentState) -> list[dict[str, Any]]:
    a = s["findings"]["application"]
    if a["grade"] in ("D", "E") and a["decision"] is None:
        return [{"tool": "create_case", "input": {"kind": "credit", "entity_id": a["application_id"]},
                 "rationale": _L(s, "Grade D/E : la politique demande l'avis d'un second analyste ; ouvrir un dossier de revue.",
                                 "Grade D/E: policy requires a second analyst; open a review case.")}]
    return []


def plan_kyc(ctx: T.ToolContext, s: AgentState) -> None:
    _must(ctx, s, "documents", "customer_documents", {"id": s["entity_id"]})
    _try(ctx, s, "customer", "customer_profile", {"id": s["entity_id"]})
    _try(ctx, s, "procedure", "knowledge_search", {"question": "justificatifs KYC KYB acceptés domicile moins de 3 mois bulletins", "scope": "private"})


def narrate_kyc(s: AgentState) -> list[str]:
    d, c = s["findings"]["documents"], s["findings"].get("customer")
    out = []
    if c:
        out.append(_L(s, f"Client {c['name']} ({c['ref']}), statut KYC {c['kyc_status']}.", f"Customer {c['name']} ({c['ref']}), KYC status {c['kyc_status']}."))
    out.append(_L(s, f"{d['count']} justificatif(s) : {d['validated']} validé(s), {d['with_failures']} avec contrôle en échec.",
                  f"{d['count']} document(s): {d['validated']} validated, {d['with_failures']} with failed controls."))
    for doc in d["documents"]:
        for f in doc["failed_checks"][:3]:
            out.append(f"• {doc['filename']} — {f['check']} : {f['detail']}")
    if d["count"] == 0:
        out.append(_L(s, "Aucun justificatif n'est rattaché à ce client.", "No document is linked to this customer."))
    out += _kb_lines(s)
    out.append(_L(s, "Les contrôles automatiques aident la revue ; ils ne valent pas vérification d'identité réglementaire.",
                  "Automatic controls support the review; they are not a regulatory identity verification."))
    return out


def propose_kyc(s: AgentState) -> list[dict[str, Any]]:
    d = s["findings"]["documents"]
    if d["with_failures"]:
        return [{"tool": "create_case", "input": {"kind": "kyc", "entity_id": s["entity_id"]},
                 "rationale": _L(s, "Des contrôles documentaires échouent : ouvrir un dossier KYC pour demander de nouveaux justificatifs.",
                                 "Document controls fail: open a KYC case to request new documents.")}]
    return []


def plan_customer(ctx: T.ToolContext, s: AgentState) -> None:
    _must(ctx, s, "customer", "customer_profile", {"id": s["entity_id"]})
    _try(ctx, s, "documents", "customer_documents", {"id": s["entity_id"]})


def narrate_customer(s: AgentState) -> list[str]:
    c = s["findings"]["customer"]
    topics = ", ".join(f"{k} ({v})" for k, v in sorted(c["message_topics"].items(), key=lambda kv: -kv[1])[:4]) or "—"
    out = [_L(s, f"{c['name']} ({c['ref']}) — segment {c['segment']}, client depuis {str(c['customer_since'])[:10]}, {c['accounts']} compte(s) "
                 f"pour un encours de {_eur(c['total_balance'])}, {c['cards']} carte(s).",
              f"{c['name']} ({c['ref']}) — {c['segment']} segment, customer since {str(c['customer_since'])[:10]}, {c['accounts']} account(s) "
              f"holding {_eur(c['total_balance'])}, {c['cards']} card(s).")]
    if c["behaviour_segment"]:
        out.append(_L(s, f"Segment comportemental : {c['behaviour_segment']}.", f"Behavioural segment: {c['behaviour_segment']}."))
    out.append(_L(s, f"{c['messages']} message(s) récent(s), sujets : {topics} ; {c['complaints']} réclamation(s).",
                  f"{c['messages']} recent message(s), topics: {topics}; {c['complaints']} complaint(s)."))
    if c["complaints"]:
        out.append(_L(s, "Point d'attention : traiter les réclamations ouvertes avant toute sollicitation commerciale.",
                      "Attention: handle open complaints before any commercial contact."))
    risk = [x for x in (("fraude", c["fraud_alerts"]), ("LCB-FT", c["aml_alerts"])) if x[1]]
    if risk:
        out.append(_L(s, "Alertes existantes : " + ", ".join(f"{k} {v}" for k, v in risk) + ".", "Existing alerts: " + ", ".join(f"{k} {v}" for k, v in risk) + "."))
    return out


def plan_forecast(ctx: T.ToolContext, s: AgentState) -> None:
    p = s.get("params") or {}
    _must(ctx, s, "forecast", "forecast", {"series": s["entity_id"] or p.get("series", "euribor_3m"), "horizon": int(p.get("horizon", 12))})


def narrate_forecast(s: AgentState) -> list[str]:
    f = s["findings"]["forecast"]
    out = [_L(s, f"Série {f['series']} : dernière valeur {f['last_value']} ({f['last_date']}). Projection à l'horizon {f['horizon_end']} :",
              f"Series {f['series']}: last value {f['last_value']} ({f['last_date']}). Projection to {f['horizon_end']}:")]
    means = []
    for m, r in f["forecasts"].items():
        if r["status"] == "ok":
            means.append(r["end_mean"])
            band = f" [{r['end_lo80']} ; {r['end_hi80']}] à 80 %" if r["end_lo80"] is not None else ""
            out.append(f"• {m} : {r['end_mean']}{band}")
        else:
            out.append(f"• {m} : " + _L(s, "échec du modèle", "model failed"))
    if len(means) > 1:
        out.append(_L(s, f"Écart entre modèles : {max(means) - min(means):.4g} — une dispersion élevée signale une incertitude de modèle.",
                      f"Spread across models: {max(means) - min(means):.4g} — a wide spread signals model uncertainty."))
    out.append(_L(s, "Ces projections sont statistiques et incertaines ; elles ne constituent ni une certitude ni un conseil financier.",
                  "These projections are statistical and uncertain; they are neither a certainty nor financial advice."))
    return out


def plan_case(ctx: T.ToolContext, s: AgentState) -> None:
    _must(ctx, s, "case", "case_detail", {"id": s["entity_id"]})


def narrate_case(s: AgentState) -> list[str]:
    c = s["findings"]["case"]
    cu = c.get("customer") or {}
    return [_L(s, f"Dossier {c['reference']} ({c['type']}, priorité {c['priority']}, statut {c['status']}) : {c['title']}.",
               f"Case {c['reference']} ({c['type']}, {c['priority']} priority, {c['status']}): {c['title']}."),
            _L(s, f"{c['events']} événement(s), {c['fraud_alerts']} alerte(s) fraude et {c['aml_alerts']} alerte(s) LCB-FT liées, {c['reports']} rapport(s).",
               f"{c['events']} event(s), {c['fraud_alerts']} fraud and {c['aml_alerts']} AML alert(s) linked, {c['reports']} report(s).")
            + (_L(s, f" Client : {cu.get('name')}.", f" Customer: {cu.get('name')}.") if cu else ""),
            _L(s, "Décision : " + (c["decision"] or "non prise") + ".", "Decision: " + (c["decision"] or "not taken") + ".")]


def propose_case(s: AgentState) -> list[dict[str, Any]]:
    c = s["findings"]["case"]
    if c["status"] == "closed":
        return []
    return [{"tool": "add_case_note", "input": {"case_id": c["case_id"], "text": "\n".join(s["narrative"])[:7900]},
             "rationale": _L(s, "Consigner cette synthèse dans le dossier.", "Record this summary in the case.")},
            {"tool": "generate_case_report", "input": {"id": c["case_id"]},
             "rationale": _L(s, "Produire le rapport PDF d'investigation (horodaté, marqué DEMO si applicable).",
                             "Produce the investigation PDF report (timestamped, marked DEMO where applicable).")}]


def plan_models(ctx: T.ToolContext, s: AgentState) -> None:
    _must(ctx, s, "models", "models_status", {})


def narrate_models(s: AgentState) -> list[str]:
    ms = s["findings"]["models"]["models"]
    prod = [m for m in ms if m["production"]]
    out = [_L(s, f"{len(prod)} modèle(s) en production sur {len(ms)} inventorié(s) ; {sum(m['pending_review'] for m in ms)} version(s) en attente de revue.",
              f"{len(prod)} model(s) in production out of {len(ms)}; {sum(m['pending_review'] for m in ms)} version(s) awaiting review.")]
    for m in ms:
        if not m["production"]:
            out.append(_L(s, f"• {m['name']} : aucune version en production.", f"• {m['name']}: no production version."))
            continue
        h = m["production"]["headline"]
        dr = m["drift"] or {}
        drift_txt = {"stable": _L(s, "stable", "stable"), "moderate": _L(s, "dérive modérée", "moderate drift"),
                     "significant": _L(s, "dérive significative", "significant drift"), "insufficient_data": _L(s, "données insuffisantes", "insufficient data"),
                     "no_reference_profile": _L(s, "pas de profil de référence", "no reference profile")}.get(dr.get("status") or "", dr.get("status") or "—")
        live = m["live"] or {}
        out.append(f"• {m['name']} v{m['production']['version']} — {h['label'] + ' ' + str(h['value']) if h else ''} — "
                   + _L(s, f"{live.get('inferences', 0)} inférences sur 30 j, PSI max {dr.get('max_psi')}, {drift_txt}.",
                        f"{live.get('inferences', 0)} inferences over 30 d, max PSI {dr.get('max_psi')}, {drift_txt}."))
    return out


def propose_models(s: AgentState) -> list[dict[str, Any]]:
    props = []
    for m in s["findings"]["models"]["models"]:
        dr = m.get("drift") or {}
        if dr.get("status") == "significant":
            top = ", ".join(f"{f['feature']} (PSI {f['psi']})" for f in dr.get("top", []))
            props.append({"tool": "declare_model_incident",
                          "input": {"model_name": m["name"], "severity": "high", "title": f"Dérive significative — {m['name']}",
                                    "description": f"PSI max {dr.get('max_psi')} sur {dr.get('samples')} inférences. Variables : {top}."},
                          "rationale": _L(s, "PSI > 0,25 : déclarer un incident et évaluer un réentraînement.", "PSI > 0.25: declare an incident and assess retraining.")})
    return props


AGENTS: dict[str, AgentSpec] = {a.key: a for a in [
    AgentSpec("fraud_investigator", "Enquêteur fraude carte", "Card-fraud investigator",
              "Analyse une alerte de fraude : facteurs du modèle, historique de la carte, profil client, procédure.",
              "Analyses a fraud alert: model factors, card history, customer profile, procedure.", "fraud_alert", "fraud.read",
              plan_fraud, narrate_fraud, propose_fraud),
    AgentSpec("aml_investigator", "Enquêteur LCB-FT", "AML investigator",
              "Instruit une alerte LCB-FT : typologies, réseau de contreparties, profil et justificatifs.",
              "Investigates an AML alert: typologies, counterparty network, profile and documents.", "aml_alert", "aml.read",
              plan_aml, narrate_aml, propose_aml),
    AgentSpec("credit_analyst", "Analyste crédit", "Credit analyst",
              "Prépare la note d'analyse d'une demande : PD, facteurs, revenus, justificatifs, politique.",
              "Prepares the analysis note of an application: PD, factors, income, documents, policy.", "credit_application", "credit.read",
              plan_credit, narrate_credit, propose_credit),
    AgentSpec("kyc_reviewer", "Contrôleur KYC", "KYC reviewer",
              "Passe en revue les justificatifs d'un client et les contrôles en échec.",
              "Reviews a customer's documents and failed controls.", "customer", "documents.read", plan_kyc, narrate_kyc, propose_kyc),
    AgentSpec("customer_advisor", "Conseiller relation client", "Relationship advisor",
              "Synthèse 360 d'un client : comptes, segment, messages et réclamations.",
              "360 summary of a customer: accounts, segment, messages and complaints.", "customer", "customer.read",
              plan_customer, narrate_customer, lambda s: []),
    AgentSpec("forecast_analyst", "Analyste prévision", "Forecast analyst",
              "Projette une série macro-financière avec plusieurs modèles et commente l'incertitude.",
              "Projects a macro-financial series with several models and comments on uncertainty.", "series", "forecast.read",
              plan_forecast, narrate_forecast, lambda s: []),
    AgentSpec("case_reporter", "Rédacteur de dossier", "Case reporter",
              "Résume un dossier d'investigation et propose la note et le rapport PDF.",
              "Summarises an investigation case and proposes the note and PDF report.", "case", "cases.read",
              plan_case, narrate_case, propose_case),
    AgentSpec("model_risk_monitor", "Veille risque de modèle", "Model-risk monitor",
              "Vérifie les modèles en production, leur dérive et les revues en attente.",
              "Checks production models, their drift and pending reviews.", None, "models.read",
              plan_models, narrate_models, propose_models),
]}

ENTITY_TO_AGENT = {"fraud_alert": "fraud_investigator", "aml_alert": "aml_investigator", "credit_application": "credit_analyst",
                   "case": "case_reporter", "customer": "customer_advisor", "series": "forecast_analyst"}
KEYWORDS = [
    (r"\b(kyc|kyb|justificatif|document)", "kyc_reviewer"), (r"\b(lcb|blanchiment|aml|money laundering)", "aml_investigator"),
    (r"\b(fraude|fraud)", "fraud_investigator"), (r"\b(crédit|credit|prêt|loan)", "credit_analyst"),
    (r"\b(prévision|prevision|forecast|projection)", "forecast_analyst"), (r"\b(dossier|case)\b", "case_reporter"),
    (r"\b(modèle|model|dérive|drift)", "model_risk_monitor"), (r"\b(client|customer)", "customer_advisor"),
]
UUID_RX = re.compile(r"[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}", re.I)


# ------------------------------------------------------------------------------------------------
# Graph
# ------------------------------------------------------------------------------------------------
def build(ctx: T.ToolContext) -> Any:
    settings = get_settings()

    def guard(s: AgentState) -> AgentState:
        v = guardrails.check_input(s["request"])
        if v.status != "ok":
            return {"status": "blocked" if v.status == "blocked" else "refused", "message": v.message}
        return {"status": "running"}

    def route(s: AgentState) -> AgentState:
        agent, etype, eid = s.get("agent"), s.get("entity_type"), s.get("entity_id")
        if not eid:
            m = UUID_RX.search(s["request"])
            if m:
                eid = m.group(0)
                etype = T.resolve_entity(ctx.db, uuid.UUID(eid))
        if agent is None and etype:
            agent = ENTITY_TO_AGENT.get(etype)
            if etype == "customer" and re.search(r"\b(kyc|kyb|justificatif|document)", s["request"], re.I):
                agent = "kyc_reviewer"
        if agent is None:
            agent = next((a for rx, a in KEYWORDS if re.search(rx, s["request"], re.I)), None)
        if agent is None or agent not in AGENTS:
            return {"status": "needs_input", "message": _L(s, "Précisez la tâche (fraude, LCB-FT, crédit, KYC, client, prévision, dossier, modèles) et l'objet concerné.",
                                                           "Specify the task (fraud, AML, credit, KYC, customer, forecast, case, models) and the object concerned.")}
        spec = AGENTS[agent]
        if not ctx.principal.can(spec.permission):
            return {"agent": agent, "status": "refused", "message": _L(s, "Votre rôle ne permet pas d'utiliser cet agent.", "Your role cannot use this agent.")}
        if spec.entity_type and spec.entity_type != "series":
            if not eid:
                return {"agent": agent, "status": "needs_input", "message": _L(s, "Sélectionnez l'objet à analyser.", "Select the object to analyse.")}
            actual = etype or T.resolve_entity(ctx.db, uuid.UUID(str(eid)))
            if actual != spec.entity_type:
                return {"agent": agent, "status": "needs_input",
                        "message": _L(s, f"Cet agent attend un objet de type {spec.entity_type}.", f"This agent expects a {spec.entity_type}.")}
            etype = actual
        return {"agent": agent, "entity_type": spec.entity_type or etype, "entity_id": str(eid) if eid else None}

    def gather(s: AgentState) -> AgentState:
        st: AgentState = {**s, "findings": {}, "skipped": [], "sources": []}  # type: ignore[typeddict-item]
        AGENTS[s["agent"]].plan(ctx, st)  # type: ignore[index]
        return {"findings": st["findings"], "skipped": st["skipped"], "sources": []}

    def analyze(s: AgentState) -> AgentState:
        st: AgentState = {**s, "sources": []}  # type: ignore[typeddict-item]
        narrative = AGENTS[s["agent"]].narrate(st)  # type: ignore[index]
        planner = "deterministic"
        if llm.available():
            try:
                facts = [{"source": k, "content": json.dumps(v, default=str, ensure_ascii=False)[:3000]} for k, v in s["findings"].items()]
                text_ = llm.generate(f"{s['request']}\n\nRédige une synthèse d'enquête prudente et sourcée.", facts)
                if text_.strip():
                    narrative = [text_.strip(), "—", *narrative]
                    planner = "deterministic+llm"
            except Exception:  # provider failure → deterministic narrative only
                planner = "deterministic (llm_error)"
        return {"narrative": narrative, "sources": st["sources"], "planner": planner}

    def propose(s: AgentState) -> AgentState:
        props = [p for p in AGENTS[s["agent"]].propose(s) if p["tool"] in T.TOOLS and T.TOOLS[p["tool"]].kind == "action"]  # type: ignore[index]
        return {"proposals": props[: settings.agent_max_steps]}

    def finalize(s: AgentState) -> AgentState:
        return {"status": "awaiting_approval" if s.get("proposals") else "completed"}

    g = StateGraph(AgentState)
    for name, fn in (("guard", guard), ("route", route), ("gather", gather), ("analyze", analyze), ("propose", propose), ("finalize", finalize)):
        g.add_node(name, fn)
    g.add_edge(START, "guard")
    g.add_conditional_edges("guard", lambda s: "route" if s.get("status") == "running" else END)
    g.add_conditional_edges("route", lambda s: "gather" if s.get("status") == "running" else END)
    g.add_edge("gather", "analyze")
    g.add_edge("analyze", "propose")
    g.add_edge("propose", "finalize")
    g.add_edge("finalize", END)
    return g.compile()


def run(ctx: T.ToolContext, state: AgentState) -> AgentState:
    ctx.deadline = time.monotonic() + get_settings().agent_timeout_seconds
    graph = build(ctx)
    return graph.invoke({"findings": {}, "skipped": [], "narrative": [], "sources": [], "proposals": [], "planner": "deterministic", **state},
                        {"recursion_limit": 20})
