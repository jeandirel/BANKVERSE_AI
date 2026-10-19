"""Organisation provisioning / deactivation (control plane)."""

from __future__ import annotations

import re
import uuid
from datetime import UTC, datetime, timedelta
from typing import Any

from sqlalchemy import delete, select, text, update
from sqlalchemy.orm import Session

from bankverse.auth.rbac import ALL_MODULES
from bankverse.core.errors import Conflict, ValidationFailed
from bankverse.core.security import hash_opaque_token, new_opaque_token
from bankverse.db.base import TenantBase
from bankverse.db.models.control import (
    ApiKey,
    AuthSession,
    Invitation,
    Membership,
    Organization,
    OrgModule,
    OrgQuota,
)
from bankverse.db.session import TenantTarget, tenant_session
from bankverse.services.usage import DEFAULT_QUOTAS

SLUG_RE = re.compile(r"^[a-z0-9][a-z0-9-]{2,62}$")

DEFAULT_ORG_SETTINGS: dict[str, Any] = {
    "fraud_alert_threshold": 0.5,
    "fraud_review_cost_eur": 5.0,
    "aml_alert_threshold": 0.6,
    "default_locale": "fr",
    "data_retention_days": 3650,
    "credit_review_required_above_pd": 0.0,
    "currency": "EUR",
}


def create_organization(
    db: Session,
    *,
    slug: str,
    name: str,
    kind: str = "bank",
    country: str = "FR",
    isolation_mode: str = "shared",
    dedicated_db_secret: str | None = None,
    modules: list[str] | None = None,
    plan: str = "pilot",
) -> Organization:
    if not SLUG_RE.match(slug):
        raise ValidationFailed("Invalid slug (lowercase letters, digits, hyphens; 3-63 chars)")
    if db.execute(select(Organization.id).where(Organization.slug == slug)).first():
        raise Conflict("Organisation slug already exists")
    if isolation_mode == "dedicated" and not dedicated_db_secret:
        raise ValidationFailed("Dedicated isolation requires the name of the database secret")
    org = Organization(slug=slug, name=name, kind=kind, country=country, isolation_mode=isolation_mode,
                       dedicated_db_secret=dedicated_db_secret, plan=plan, settings=dict(DEFAULT_ORG_SETTINGS))
    db.add(org)
    db.flush()
    enabled = set(modules or ALL_MODULES)
    for m in ALL_MODULES:
        db.add(OrgModule(org_id=org.id, module=m, enabled=m in enabled, config={}))
    for metric, limit in DEFAULT_QUOTAS["demo" if kind == "demo" else "bank"].items():
        db.add(OrgQuota(org_id=org.id, metric=metric, monthly_limit=limit))
    db.flush()
    return org


def invite_admin(db: Session, org: Organization, email: str, invited_by: uuid.UUID | None) -> str:
    token = new_opaque_token()
    db.add(Invitation(org_id=org.id, email=email.lower(), role="bank_org_admin", token_hash=hash_opaque_token(token),
                      invited_by=invited_by, expires_at=datetime.now(UTC) + timedelta(days=7)))
    return token


def suspend(db: Session, org: Organization) -> None:
    org.status = "suspended"
    org.suspended_at = datetime.now(UTC)
    _revoke_access(db, org.id, "org_suspended")


def reactivate(db: Session, org: Organization) -> None:
    if org.status == "deprovisioned":
        raise Conflict("A deprovisioned organisation cannot be reactivated")
    org.status = "active"
    org.suspended_at = None


def deprovision(db: Session, org: Organization) -> None:
    """Disable access permanently. Business data is retained until an explicit purge (legal hold)."""
    org.status = "deprovisioned"
    org.deprovisioned_at = datetime.now(UTC)
    _revoke_access(db, org.id, "org_deprovisioned")
    db.execute(update(Membership).where(Membership.org_id == org.id).values(status="suspended"))


def _revoke_access(db: Session, org_id: uuid.UUID, reason: str) -> None:
    now = datetime.now(UTC)
    db.execute(update(AuthSession).where(AuthSession.org_id == org_id, AuthSession.revoked_at.is_(None))
               .values(revoked_at=now, revoke_reason=reason))
    db.execute(update(ApiKey).where(ApiKey.org_id == org_id, ApiKey.revoked_at.is_(None)).values(revoked_at=now))
    db.execute(update(Invitation).where(Invitation.org_id == org_id, Invitation.accepted_at.is_(None),
                                        Invitation.revoked_at.is_(None)).values(revoked_at=now))


def purge_tenant_data(org: Organization) -> dict[str, int]:
    """Irreversibly deletes every business row of a deprovisioned organisation (RLS-scoped)."""
    if org.status != "deprovisioned":
        raise Conflict("Only deprovisioned organisations can be purged")
    counts: dict[str, int] = {}
    target = TenantTarget(org.id, org.isolation_mode, org.dedicated_db_secret)
    with tenant_session(target, owner=True) as s:
        # Delete children first (reverse dependency order).
        for table in reversed(TenantBase.metadata.sorted_tables):
            if "org_id" in table.c:
                res = s.execute(delete(table).where(table.c.org_id == org.id))
                counts[table.name] = res.rowcount or 0
        s.execute(text("SELECT 1"))
    return counts
