"""Bank organisation administration: members, invitations, API keys, modules, quotas, audit."""

from __future__ import annotations

import uuid
from datetime import UTC, datetime, timedelta
from typing import Any

from fastapi import APIRouter, Depends, Query, Request
from pydantic import BaseModel, EmailStr, Field
from sqlalchemy import func, select
from sqlalchemy.orm import Session

from bankverse.auth.deps import Principal, client_ip, control_db, require
from bankverse.auth.rbac import ALL_MODULES, ASSIGNABLE_BY_BANK_ADMIN, ROLE_LABELS, ROLE_PERMISSIONS, Role
from bankverse.core.errors import Conflict, Forbidden, NotFound, ValidationFailed
from bankverse.core.security import generate_api_key, hash_opaque_token, new_opaque_token
from bankverse.db.models.control import (
    ApiKey,
    AuditLog,
    Invitation,
    Membership,
    Organization,
    OrgModule,
    SecurityEvent,
    User,
)
from bankverse.services import audit, usage

router = APIRouter(prefix="/org", tags=["organization"])


def _audit(principal: Principal, request: Request, action: str, db: Session, **kw: Any) -> None:
    audit.record(action, org_id=principal.org_id, actor_user_id=principal.user_id, actor_type=principal.kind,
                 actor_label=principal.label, ip=client_ip(request), session=db, **kw)


@router.get("")
def get_org(principal: Principal = Depends(require("org.read")), db: Session = Depends(control_db)) -> dict[str, Any]:
    org = db.get(Organization, principal.org_id)
    assert org is not None
    members = db.execute(
        select(func.count()).select_from(Membership).where(Membership.org_id == org.id, Membership.status == "active")
    ).scalar_one()
    modules = db.execute(select(OrgModule).where(OrgModule.org_id == org.id)).scalars().all()
    return {
        "id": str(org.id), "slug": org.slug, "name": org.name, "kind": org.kind, "country": org.country,
        "status": org.status, "plan": org.plan, "isolation_mode": org.isolation_mode, "data_region": org.data_region,
        "created_at": org.created_at, "members": members, "settings": org.settings,
        "modules": [{"module": m.module, "enabled": m.enabled, "config": m.config} for m in modules],
    }


class OrgSettingsIn(BaseModel):
    name: str | None = Field(default=None, min_length=2, max_length=200)
    settings: dict[str, Any] | None = None


ALLOWED_SETTINGS = {"fraud_alert_threshold", "fraud_review_cost_eur", "aml_alert_threshold", "default_locale",
                    "data_retention_days", "credit_review_required_above_pd", "currency"}


@router.patch("")
def update_org(body: OrgSettingsIn, request: Request, principal: Principal = Depends(require("org.settings.manage")),
               db: Session = Depends(control_db)) -> dict[str, Any]:
    org = db.get(Organization, principal.org_id)
    assert org is not None
    if body.name:
        org.name = body.name
    if body.settings is not None:
        unknown = set(body.settings) - ALLOWED_SETTINGS
        if unknown:
            raise ValidationFailed(f"Unknown settings: {sorted(unknown)}")
        merged = dict(org.settings or {})
        merged.update(body.settings)
        org.settings = merged
    _audit(principal, request, "org.settings_updated", db, resource_type="organization", resource_id=str(org.id),
           details={"name": body.name, "settings": body.settings})
    return {"status": "ok", "settings": org.settings}


class ModuleToggleIn(BaseModel):
    enabled: bool


@router.put("/modules/{module}")
def toggle_module(module: str, body: ModuleToggleIn, request: Request,
                  principal: Principal = Depends(require("org.settings.manage")),
                  db: Session = Depends(control_db)) -> dict[str, Any]:
    if module not in ALL_MODULES:
        raise NotFound("Unknown module")
    row = db.get(OrgModule, (principal.org_id, module))
    if row is None:
        row = OrgModule(org_id=principal.org_id, module=module, enabled=body.enabled, config={})
        db.add(row)
    row.enabled = body.enabled
    _audit(principal, request, "org.module_toggled", db, resource_type="module", resource_id=module,
           details={"enabled": body.enabled})
    return {"module": module, "enabled": body.enabled}


# --- Members -------------------------------------------------------------------------------------
@router.get("/members")
def list_members(principal: Principal = Depends(require("org.read")), db: Session = Depends(control_db)) -> list[dict[str, Any]]:
    rows = db.execute(
        select(Membership, User).join(User, User.id == Membership.user_id)
        .where(Membership.org_id == principal.org_id).order_by(User.full_name)
    ).all()
    return [
        {"membership_id": str(m.id), "user_id": str(u.id), "email": u.email, "full_name": u.full_name,
         "role": m.role, "role_label": ROLE_LABELS.get(m.role, {}), "status": m.status, "mfa_enabled": u.mfa_enabled,
         "last_login_at": u.last_login_at, "joined_at": m.created_at}
        for m, u in rows
    ]


class MemberUpdateIn(BaseModel):
    role: str | None = None
    status: str | None = Field(default=None, pattern="^(active|suspended)$")


@router.patch("/members/{membership_id}")
def update_member(membership_id: uuid.UUID, body: MemberUpdateIn, request: Request,
                  principal: Principal = Depends(require("org.members.manage")),
                  db: Session = Depends(control_db)) -> dict[str, str]:
    m = db.get(Membership, membership_id)
    if m is None or m.org_id != principal.org_id:
        raise NotFound("Member not found")
    if m.user_id == principal.user_id:
        raise Forbidden("You cannot change your own role or status", code="self_modification")
    if body.role is not None:
        if body.role not in ASSIGNABLE_BY_BANK_ADMIN and not principal.is_platform_admin:
            raise Forbidden("Role cannot be assigned")
        m.role = body.role
    if body.status is not None:
        m.status = body.status
    _audit(principal, request, "org.member_updated", db, resource_type="membership", resource_id=str(m.id),
           details={"role": body.role, "status": body.status})
    return {"status": "ok"}


@router.delete("/members/{membership_id}")
def remove_member(membership_id: uuid.UUID, request: Request, principal: Principal = Depends(require("org.members.manage")),
                  db: Session = Depends(control_db)) -> dict[str, str]:
    m = db.get(Membership, membership_id)
    if m is None or m.org_id != principal.org_id:
        raise NotFound("Member not found")
    if m.user_id == principal.user_id:
        raise Forbidden("You cannot remove yourself", code="self_modification")
    db.delete(m)
    _audit(principal, request, "org.member_removed", db, resource_type="membership", resource_id=str(membership_id))
    return {"status": "ok"}


# --- Invitations ---------------------------------------------------------------------------------
class InviteIn(BaseModel):
    email: EmailStr
    role: str


@router.get("/invitations")
def list_invitations(principal: Principal = Depends(require("org.members.manage")),
                     db: Session = Depends(control_db)) -> list[dict[str, Any]]:
    rows = db.execute(
        select(Invitation).where(Invitation.org_id == principal.org_id).order_by(Invitation.created_at.desc()).limit(200)
    ).scalars()
    now = datetime.now(UTC)
    return [
        {"id": str(i.id), "email": i.email, "role": i.role, "created_at": i.created_at, "expires_at": i.expires_at,
         "status": "accepted" if i.accepted_at else "revoked" if i.revoked_at else "expired" if i.expires_at < now else "pending"}
        for i in rows
    ]


@router.post("/invitations")
def invite(body: InviteIn, request: Request, principal: Principal = Depends(require("org.members.manage")),
           db: Session = Depends(control_db)) -> dict[str, Any]:
    if body.role not in ASSIGNABLE_BY_BANK_ADMIN:
        raise Forbidden("Role cannot be assigned")
    email = body.email.lower()
    existing = db.execute(
        select(Membership).join(User, User.id == Membership.user_id)
        .where(User.email == email, Membership.org_id == principal.org_id)
    ).scalar_one_or_none()
    if existing:
        raise Conflict("User is already a member")
    token = new_opaque_token()
    inv = Invitation(org_id=principal.org_id, email=email, role=body.role, token_hash=hash_opaque_token(token),
                     invited_by=principal.user_id, expires_at=datetime.now(UTC) + timedelta(days=7))
    db.add(inv)
    db.flush()
    _audit(principal, request, "org.invitation_created", db, resource_type="invitation", resource_id=str(inv.id),
           details={"email": email, "role": body.role})
    # No e-mail provider is configured in this environment: the one-time link is returned to the admin
    # (shown once) so it can be transmitted through an approved channel. With Amazon SES configured,
    # the link would be e-mailed instead and not returned.
    return {"id": str(inv.id), "invite_path": f"/invite/{token}", "expires_at": inv.expires_at,
            "delivery": "manual (no e-mail provider configured)"}


@router.delete("/invitations/{invitation_id}")
def revoke_invitation(invitation_id: uuid.UUID, request: Request,
                      principal: Principal = Depends(require("org.members.manage")),
                      db: Session = Depends(control_db)) -> dict[str, str]:
    inv = db.get(Invitation, invitation_id)
    if inv is None or inv.org_id != principal.org_id:
        raise NotFound("Invitation not found")
    inv.revoked_at = datetime.now(UTC)
    _audit(principal, request, "org.invitation_revoked", db, resource_type="invitation", resource_id=str(inv.id))
    return {"status": "ok"}


@router.get("/roles")
def roles(principal: Principal = Depends(require("org.read"))) -> list[dict[str, Any]]:
    return [
        {"role": r.value, "label": ROLE_LABELS[r], "permissions": sorted(ROLE_PERMISSIONS[r]),
         "assignable": r.value in ASSIGNABLE_BY_BANK_ADMIN}
        for r in Role
        if r is not Role.PLATFORM_SUPER_ADMIN or principal.is_platform_admin
    ]


# --- API keys / service identities ---------------------------------------------------------------
class ApiKeyIn(BaseModel):
    name: str = Field(min_length=3, max_length=120)
    role: str
    expires_in_days: int | None = Field(default=90, ge=1, le=365)


@router.get("/api-keys")
def list_api_keys(principal: Principal = Depends(require("org.apikeys.manage")),
                  db: Session = Depends(control_db)) -> list[dict[str, Any]]:
    rows = db.execute(select(ApiKey).where(ApiKey.org_id == principal.org_id).order_by(ApiKey.created_at.desc())).scalars()
    return [
        {"id": str(k.id), "name": k.name, "prefix": k.prefix, "role": k.role, "created_at": k.created_at,
         "expires_at": k.expires_at, "last_used_at": k.last_used_at, "revoked": k.revoked_at is not None}
        for k in rows
    ]


@router.post("/api-keys")
def create_api_key(body: ApiKeyIn, request: Request, principal: Principal = Depends(require("org.apikeys.manage")),
                   db: Session = Depends(control_db)) -> dict[str, Any]:
    if body.role not in ASSIGNABLE_BY_BANK_ADMIN or body.role == Role.BANK_ORG_ADMIN:
        raise Forbidden("Service identities cannot hold administrative roles")
    full, prefix, key_hash = generate_api_key()
    key = ApiKey(org_id=principal.org_id, name=body.name, prefix=prefix, key_hash=key_hash, role=body.role,
                 created_by=principal.user_id,
                 expires_at=datetime.now(UTC) + timedelta(days=body.expires_in_days) if body.expires_in_days else None)
    db.add(key)
    db.flush()
    _audit(principal, request, "org.api_key_created", db, resource_type="api_key", resource_id=str(key.id),
           details={"name": body.name, "role": body.role, "prefix": prefix})
    return {"id": str(key.id), "api_key": full, "prefix": prefix, "note": "Shown once. Store it in a secrets manager."}


@router.delete("/api-keys/{key_id}")
def revoke_api_key(key_id: uuid.UUID, request: Request, principal: Principal = Depends(require("org.apikeys.manage")),
                   db: Session = Depends(control_db)) -> dict[str, str]:
    key = db.get(ApiKey, key_id)
    if key is None or key.org_id != principal.org_id:
        raise NotFound("API key not found")
    key.revoked_at = datetime.now(UTC)
    _audit(principal, request, "org.api_key_revoked", db, resource_type="api_key", resource_id=str(key.id))
    return {"status": "ok"}


# --- Usage, audit, security ----------------------------------------------------------------------
@router.get("/usage")
def get_usage(principal: Principal = Depends(require("org.read")), db: Session = Depends(control_db)) -> list[dict[str, object]]:
    return usage.usage_for(db, principal.org_id)


@router.get("/audit")
def audit_log(
    principal: Principal = Depends(require("audit.read")),
    db: Session = Depends(control_db),
    action: str | None = Query(default=None, max_length=80),
    outcome: str | None = Query(default=None, max_length=16),
    limit: int = Query(default=100, ge=1, le=500),
    offset: int = Query(default=0, ge=0),
) -> dict[str, Any]:
    q = select(AuditLog).where(AuditLog.org_id == principal.org_id)
    if action:
        q = q.where(AuditLog.action.like(f"{action}%"))
    if outcome:
        q = q.where(AuditLog.outcome == outcome)
    total = db.execute(select(func.count()).select_from(q.subquery())).scalar_one()
    rows = db.execute(q.order_by(AuditLog.id.desc()).limit(limit).offset(offset)).scalars()
    return {
        "total": total,
        "items": [
            {"id": r.id, "created_at": r.created_at, "actor": r.actor_label, "actor_type": r.actor_type,
             "action": r.action, "resource_type": r.resource_type, "resource_id": r.resource_id,
             "outcome": r.outcome, "ip": r.ip, "details": r.details, "hash": r.hash[:16]}
            for r in rows
        ],
    }


@router.get("/security-events")
def security_events(principal: Principal = Depends(require("audit.read")),
                    db: Session = Depends(control_db)) -> list[dict[str, Any]]:
    member_ids = select(Membership.user_id).where(Membership.org_id == principal.org_id)
    rows = db.execute(
        select(SecurityEvent)
        .where((SecurityEvent.org_id == principal.org_id) | (SecurityEvent.user_id.in_(member_ids)))
        .order_by(SecurityEvent.created_at.desc()).limit(200)
    ).scalars()
    return [
        {"id": str(e.id), "kind": e.kind, "severity": e.severity, "created_at": e.created_at, "ip": e.ip,
         "details": e.details, "acknowledged": e.acknowledged_at is not None}
        for e in rows
    ]
