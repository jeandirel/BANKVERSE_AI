"""Input / evidence / output guardrails for the copilot. Deterministic rules, logged with their reason."""

from __future__ import annotations

import re
from dataclasses import dataclass

INJECTION = [
    r"ignore\s+(all\s+|any\s+|the\s+)?(previous|prior|above|earlier)\s+(instructions|prompts?|rules)",
    r"disregard\s+(all\s+|the\s+)?(previous|prior|above)", r"forget\s+(all\s+)?(your|the)\s+(instructions|rules)",
    r"(reveal|show|print|repeat)\s+(me\s+)?(your|the)\s+(system\s+)?(prompt|instructions)", r"\bsystem\s+prompt\b",
    r"you\s+are\s+now\s+", r"\bjailbreak\b", r"\bDAN\b", r"developer\s+mode",
    r"ignore[rz]?\s+(toutes?\s+)?(les\s+)?(instructions|consignes|règles)\s*(précédentes|ci-dessus)?",
    r"oublie[rz]?\s+(toutes?\s+)?(tes|vos|les)\s+(instructions|consignes|règles)", r"(révèle|affiche|montre)[rz]?\s+(ton|votre|le)\s+prompt",
    r"tu\s+es\s+maintenant\s+", r"<\s*/?\s*(system|assistant)\s*>",
]
ADVICE = [
    r"\bshould\s+i\s+(buy|sell|short|invest)", r"\b(buy|sell)\s+(the\s+)?(stock|shares?)\b", r"\bprice\s+target\b",
    r"\b(dois|devrais|faut)[- ]?(je|il)\s+(acheter|vendre|investir)", r"\bconseil\s+(en\s+)?investissement", r"\brecommande[rz]?\s+(une\s+)?action",
    r"\bquelle\s+action\s+acheter",
]
ACTIONS = [
    r"\b(execute|make|send|initiate)\s+(a\s+)?(wire|transfer|payment)\b", r"\b(effectue|fais|envoie|lance)[rz]?\s+(un\s+)?(virement|paiement)\b",
    r"\b(approve|reject)\s+(the\s+)?(loan|credit)\b", r"\b(accorde|refuse)[rz]?\s+(le\s+)?(crédit|prêt)\b",
]
PII = [(re.compile(r"\b[A-Z]{2}\d{2}(?:\s?[A-Z0-9]{4}){3,7}(?:\s?[A-Z0-9]{1,3})?\b"), "[IBAN]"),
       (re.compile(r"\b(?:\d[ -]?){13,19}\b"), "[CARD]"),
       (re.compile(r"[\w.+-]+@[\w-]+\.[\w.-]+"), "[EMAIL]"),
       (re.compile(r"(?:\+33|0)\s?[1-9](?:[\s.-]?\d{2}){4}"), "[PHONE]")]

_INJ = [re.compile(p, re.I) for p in INJECTION]
_ADV = [re.compile(p, re.I) for p in ADVICE]
_ACT = [re.compile(p, re.I) for p in ACTIONS]


@dataclass
class Verdict:
    status: str  # ok | blocked | refused
    reason: str | None = None
    message: str | None = None


def check_input(q: str) -> Verdict:
    if len(q.strip()) < 3:
        return Verdict("refused", "too_short", "Question trop courte.")
    if any(r.search(q) for r in _INJ):
        return Verdict("blocked", "prompt_injection",
                       "Requête bloquée : elle tente de modifier les consignes du copilote. Reformulez une question sur vos documents.")
    if any(r.search(q) for r in _ACT):
        return Verdict("refused", "action_request",
                       "Le copilote n'exécute aucune opération (virement, décision de crédit…). Il fournit des éléments sourcés ; "
                       "la décision et l'exécution restent humaines, dans les outils prévus.")
    if any(r.search(q) for r in _ADV):
        return Verdict("refused", "investment_advice",
                       "Le copilote ne fournit pas de conseil en investissement. Il peut retrouver et citer des informations publiées.")
    return Verdict("ok")


def evidence_is_suspicious(text: str) -> bool:
    """Retrieved content that contains instructions aimed at the model is never passed to an LLM."""
    return any(r.search(text) for r in _INJ)


def mask_pii(text: str) -> str:
    for rx, repl in PII:
        text = rx.sub(repl, text)
    return text
