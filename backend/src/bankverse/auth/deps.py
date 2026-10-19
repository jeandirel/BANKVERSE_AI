"""FastAPI dependencies resolving *who* is calling and *what* they may do — on every request.

Security properties:
* the organisation (tenant) is never taken from a client parameter: it comes from the signed
  session token and is re-validated against an active membership in the database at each request;
* cookie-authenticated unsafe requests require a matching double-submit CSRF token and an allowed
  Origin;
* API keys are hashed, scoped to one organisation and one role, revocable and expirable;
* permission checks also verify that the module is enabled for the organisation.
"""

from __future__ import annotations

import uuid
from collections.abc import Callable, Iterator
from dataclasses import dataclass, field
from datetime import UTC, datetime

from fastapi import Depends, Request
from sqlalchemy import select, update
from sqlalchemy.orm import Session

from bankverse.auth.rbac import module_of, permissions_for
from bankverse.core.config import get_settings
from bankverse.core.errors import Forbidden, Unauthorized
from bankverse.core.logging import org_id_var, user_id_var
from bankverse.core.ratelimit import limiter
from bankverse.core.security import (
    InvalidTokenError,
    constant_time_equals,
    decode_token,
    hash_opaque_token,
    parse_api_key,
)
from bankverse.db.models.control import ApiKey, AuthSession, Membership, Organization, OrgModule, User
from bankverse.db.session import TenantTarget, control_session, open_tenant_session
from bankverse.services import audit

ACCESS_COOKIE = "bv_access"
REFRESH_COOKIE = "bv_refresh"
CSRF_COOKIE = "bv_csrf"
CSRF_HEADER = "x-csrf-token"
SAFE_METHODS = {"GET", "HEAD", "OPTIONS"}


@dataclass(frozen=True)
class Principal:
    kind: str  # user | api_key
    org_id: uuid.UUID
    org_slug: str
    org_name: str
    org_kind: str
    role: str
    permissions: frozenset[str]
    enabled_modules: frozenset[str]
    label: str
    user_id: uuid.UUID | None = None
    api_key_id: uuid.UUID | None = None
    session_id: uuid.UUID | None = None
    mfa_verified: bool = False
    is_platform_admin: bool = False
    isolation_mode: str = "shared"
    dedicated_db_secret: str | None = None
    locale: str = "fr"
    extra: dict[str, object] = field(default_factory=dict)

    @property
    def actor_id(self) -> uuid.UUID:
        return self.user_id or self.api_key_id or uuid.UUID(int=0)

    @property
    def tenant(self) -> TenantTarget:
        return TenantTarget(self.org_id, self.isolation_mode, self.dedicated_db_secret)

    def can(self, permission: str) -> bool:
        if permission not in self.permissions:
            return False
        mod = module_of(permission)
        return mod is None or mod in self.enabled_modules


def client_ip(request: Request) -> str | None:
    # Behind the AWS ALB the left-most X-Forwarded-For entry is the client; trusted only because the
    # ALB overwrites/appends it and the API is not reachable directly (private subnets).
    fwd = request.headers.get("x-forwarded-for")
    if fwd:
        return fwd.split(",")[0].strip()[:64]
    return request.client.host if request.client else None


def _load_org_context(db: Session, org_id: uuid.UUID) -> tuple[Organization, frozenset[str]]:
    org = db.get(Organization, org_id)
    if org is None or org.status != "active":
        raise Forbidden("Organisation is not active", code="org_inactive")
    modules = frozenset(
        db.execute(select(OrgModule.module).where(OrgModule.org_id == org_id, OrgModule.enabled.is_(True))).scalars()
    )
    return org, modules


def _check_csrf(request: Request) -> None:
    if request.method in SAFE_METHODS:
        return
    cookie = request.cookies.get(CSRF_COOKIE)
    header = request.headers.get(CSRF_HEADER)
    if not cookie or not header or not constant_time_equals(cookie, header):
        raise Forbidden("CSRF token missing or invalid", code="csrf_failed")
    origin = request.headers.get("origin")
    if origin and origin not in get_settings().allowed_origins:
        raise Forbidden("Origin not allowed", code="origin_forbidden")


def _principal_from_api_key(raw: str, request: Request) -> Principal:
    prefix = parse_api_key(raw)
    if prefix is None:
        raise Unauthorized("Invalid API key")
    with control_session() as db:
        key = db.execute(select(ApiKey).where(ApiKey.prefix == prefix)).scalar_one_or_none()
        now = datetime.now(UTC)
        if (
            key is None
            or not constant_time_equals(key.key_hash, hash_opaque_token(raw))
            or key.revoked_at is not None
            or (key.expires_at is not None and key.expires_at < now)
        ):
            audit.record("auth.api_key", outcome="failure", ip=client_ip(request), actor_type="api_key",
                         details={"prefix": prefix})
            raise Unauthorized("Invalid API key")
        org, modules = _load_org_context(db, key.org_id)
        db.execute(update(ApiKey).where(ApiKey.id == key.id).values(last_used_at=now))
        return Principal(
            kind="api_key",
            org_id=org.id,
            org_slug=org.slug,
            org_name=org.name,
            org_kind=org.kind,
            role=key.role,
            permissions=permissions_for(key.role),
            enabled_modules=modules,
            label=f"api-key:{key.name}",
            api_key_id=key.id,
            mfa_verified=True,
            isolation_mode=org.isolation_mode,
            dedicated_db_secret=org.dedicated_db_secret,
        )


def _principal_from_cookie(token: str, request: Request) -> Principal:
    try:
        claims = decode_token(token, "access")
    except InvalidTokenError as exc:
        raise Unauthorized("Session expired or invalid", code="session_invalid") from exc
    if not claims.org or not claims.sid:
        raise Unauthorized("Session has no organisation", code="session_invalid")
    _check_csrf(request)
    user_id, org_id, sid = uuid.UUID(claims.sub), uuid.UUID(claims.org), uuid.UUID(claims.sid)
    with control_session() as db:
        sess = db.get(AuthSession, sid)
        now = datetime.now(UTC)
        if sess is None or sess.revoked_at is not None or sess.expires_at < now or sess.user_id != user_id:
            raise Unauthorized("Session revoked", code="session_invalid")
        user = db.get(User, user_id)
        if user is None or user.status != "active" or (user.locked_until and user.locked_until > now):
            raise Unauthorized("Account disabled", code="account_disabled")
        membership = db.execute(
            select(Membership).where(
                Membership.user_id == user_id, Membership.org_id == org_id, Membership.status == "active"
            )
        ).scalar_one_or_none()
        if membership is None:
            raise Forbidden("No active membership in this organisation", code="no_membership")
        org, modules = _load_org_context(db, org_id)
        settings = get_settings()
        if (
            settings.enforce_mfa
            and membership.role in settings.mfa_required_roles
            and not claims.mfa
        ):
            raise Forbidden("Multi-factor authentication required for this role", code="mfa_required")
        return Principal(
            kind="user",
            org_id=org.id,
            org_slug=org.slug,
            org_name=org.name,
            org_kind=org.kind,
            role=membership.role,
            permissions=permissions_for(membership.role),
            enabled_modules=modules,
            label=user.email,
            user_id=user.id,
            session_id=sid,
            mfa_verified=claims.mfa,
            is_platform_admin=user.is_platform_admin,
            isolation_mode=org.isolation_mode,
            dedicated_db_secret=org.dedicated_db_secret,
            locale=user.locale,
        )


def get_principal(request: Request) -> Principal:
    cached = getattr(request.state, "principal", None)
    if cached is not None:
        return cached
    auth = request.headers.get("authorization", "")
    if auth.lower().startswith("bearer bvk_"):
        principal = _principal_from_api_key(auth[7:].strip(), request)
    else:
        token = request.cookies.get(ACCESS_COOKIE)
        if not token:
            raise Unauthorized("Authentication required")
        principal = _principal_from_cookie(token, request)
    limiter.hit(f"api:{principal.kind}:{principal.actor_id}", get_settings().rate_limit_api_per_minute)
    request.state.principal = principal
    org_id_var.set(str(principal.org_id))
    user_id_var.set(str(principal.actor_id))
    return principal


def require(permission: str) -> Callable[..., Principal]:
    def _dep(request: Request, principal: Principal = Depends(get_principal)) -> Principal:
        if not principal.can(permission):
            audit.record(
                "access.denied",
                org_id=principal.org_id,
                actor_user_id=principal.user_id,
                actor_type=principal.kind,
                actor_label=principal.label,
                outcome="denied",
                ip=client_ip(request),
                details={"permission": permission, "path": request.url.path, "role": principal.role},
            )
            mod = module_of(permission)
            if mod and mod not in principal.enabled_modules and permission in principal.permissions:
                raise Forbidden(f"Module '{mod}' is not enabled for this organisation", code="module_disabled")
            raise Forbidden(f"Missing permission: {permission}", code="permission_denied")
        return principal

    return _dep


def require_platform_admin(principal: Principal = Depends(get_principal)) -> Principal:
    if not (principal.is_platform_admin and principal.can("platform.admin") and principal.mfa_verified):
        raise Forbidden("Platform administration requires a platform super admin with MFA", code="permission_denied")
    return principal


def tenant_db(principal: Principal = Depends(get_principal)) -> Iterator[Session]:
    """Tenant-scoped session: RLS context bound to the principal's organisation."""
    session = open_tenant_session(principal.tenant)
    try:
        yield session
        session.commit()
    except Exception:
        session.rollback()
        raise
    finally:
        session.close()


def control_db() -> Iterator[Session]:
    with control_session() as s:
        yield s
