"""Role-based access control.

Roles and permissions are defined in code (versioned, reviewed, tested) rather than editable in the
database, so that a compromised admin account cannot silently grant itself new capabilities.
Every permission check happens server-side in :mod:`bankverse.auth.deps`.
"""

from __future__ import annotations

from enum import StrEnum


class Role(StrEnum):
    PLATFORM_SUPER_ADMIN = "platform_super_admin"
    BANK_ORG_ADMIN = "bank_org_admin"
    RISK_MANAGER = "risk_manager"
    FRAUD_ANALYST = "fraud_analyst"
    AML_ANALYST = "aml_analyst"
    CREDIT_ANALYST = "credit_analyst"
    DATA_SCIENTIST = "data_scientist"
    COMPLIANCE_OFFICER = "compliance_officer"
    AUDITOR = "auditor"


class Module(StrEnum):
    DATA = "data"
    FRAUD = "fraud"
    AML = "aml"
    CREDIT = "credit"
    FORECASTING = "forecasting"
    DOCUMENTS = "documents"
    CUSTOMER = "customer"
    COPILOT = "copilot"
    AGENTS = "agents"
    GOVERNANCE = "governance"


ALL_MODULES: tuple[str, ...] = tuple(m.value for m in Module)

# Permission catalogue: "<area>.<action>"
READ_PERMISSIONS = {
    "dashboard.read",
    "fraud.read",
    "aml.read",
    "credit.read",
    "forecast.read",
    "documents.read",
    "customer.read",
    "cases.read",
    "reports.read",
    "data.read",
    "models.read",
    "governance.read",
    "org.read",
}

ALL_PERMISSIONS = READ_PERMISSIONS | {
    "fraud.triage",
    "fraud.simulate",
    "aml.investigate",
    "credit.assess",
    "forecast.run",
    "documents.upload",
    "documents.validate",
    "copilot.use",
    "agents.use",
    "agents.approve",
    "cases.write",
    "cases.decide",
    "reports.create",
    "data.manage",
    "models.train",
    "models.approve",
    "models.deploy",
    "governance.manage",
    "org.members.manage",
    "org.settings.manage",
    "org.apikeys.manage",
    "audit.read",
    "platform.admin",
}

_ANALYST_COMMON = {"dashboard.read", "org.read", "cases.read", "cases.write", "reports.read", "reports.create",
                   "copilot.use", "agents.use", "customer.read"}

ROLE_PERMISSIONS: dict[Role, frozenset[str]] = {
    Role.PLATFORM_SUPER_ADMIN: frozenset(ALL_PERMISSIONS),
    Role.BANK_ORG_ADMIN: frozenset(
        READ_PERMISSIONS
        | {"org.members.manage", "org.settings.manage", "org.apikeys.manage", "audit.read", "data.manage",
           "copilot.use"}
    ),
    Role.RISK_MANAGER: frozenset(
        READ_PERMISSIONS
        | {"cases.write", "cases.decide", "reports.create", "forecast.run", "credit.assess", "fraud.triage",
           "aml.investigate", "agents.use", "agents.approve", "copilot.use", "models.approve", "models.deploy",
           "governance.manage"}
    ),
    Role.FRAUD_ANALYST: frozenset(_ANALYST_COMMON | {"fraud.read", "fraud.triage", "fraud.simulate",
                                                     "documents.read", "data.read"}),
    Role.AML_ANALYST: frozenset(_ANALYST_COMMON | {"aml.read", "aml.investigate", "documents.read",
                                                   "documents.upload", "data.read"}),
    Role.CREDIT_ANALYST: frozenset(_ANALYST_COMMON | {"credit.read", "credit.assess", "documents.read",
                                                      "documents.upload", "documents.validate", "forecast.read"}),
    Role.DATA_SCIENTIST: frozenset(
        {"dashboard.read", "org.read", "data.read", "data.manage", "models.read", "models.train",
         "forecast.read", "forecast.run", "governance.read", "fraud.read", "aml.read", "credit.read",
         "customer.read", "documents.read", "copilot.use", "agents.use", "reports.read", "reports.create"}
    ),
    Role.COMPLIANCE_OFFICER: frozenset(
        READ_PERMISSIONS
        | {"aml.investigate", "cases.write", "cases.decide", "reports.create", "agents.use", "agents.approve",
           "copilot.use", "audit.read", "governance.manage", "documents.validate", "models.approve"}
    ),
    Role.AUDITOR: frozenset(READ_PERMISSIONS | {"audit.read"}),
}

# Which module each permission area belongs to (used to enforce per-organisation module activation).
PERMISSION_MODULE: dict[str, str] = {
    "fraud": Module.FRAUD,
    "aml": Module.AML,
    "credit": Module.CREDIT,
    "forecast": Module.FORECASTING,
    "documents": Module.DOCUMENTS,
    "customer": Module.CUSTOMER,
    "copilot": Module.COPILOT,
    "agents": Module.AGENTS,
    "governance": Module.GOVERNANCE,
    "models": Module.GOVERNANCE,
    "data": Module.DATA,
}

ROLE_LABELS: dict[str, dict[str, str]] = {
    Role.PLATFORM_SUPER_ADMIN: {"en": "Platform Super Admin", "fr": "Super administrateur plateforme"},
    Role.BANK_ORG_ADMIN: {"en": "Bank Organization Admin", "fr": "Administrateur de la banque"},
    Role.RISK_MANAGER: {"en": "Risk Manager", "fr": "Responsable des risques"},
    Role.FRAUD_ANALYST: {"en": "Fraud Analyst", "fr": "Analyste fraude"},
    Role.AML_ANALYST: {"en": "AML Analyst", "fr": "Analyste LCB-FT"},
    Role.CREDIT_ANALYST: {"en": "Credit Analyst", "fr": "Analyste crédit"},
    Role.DATA_SCIENTIST: {"en": "Data Scientist", "fr": "Data scientist"},
    Role.COMPLIANCE_OFFICER: {"en": "Compliance Officer", "fr": "Responsable conformité"},
    Role.AUDITOR: {"en": "Read-only Auditor", "fr": "Auditeur (lecture seule)"},
}


def permissions_for(role: str) -> frozenset[str]:
    try:
        return ROLE_PERMISSIONS[Role(role)]
    except ValueError:
        return frozenset()


def module_of(permission: str) -> str | None:
    return PERMISSION_MODULE.get(permission.split(".", 1)[0])


# Roles a bank admin may assign (cannot create platform super admins).
ASSIGNABLE_BY_BANK_ADMIN = [r.value for r in Role if r is not Role.PLATFORM_SUPER_ADMIN]
