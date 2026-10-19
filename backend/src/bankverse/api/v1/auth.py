"""Authentication endpoints (cookie sessions + CSRF, TOTP MFA, org switching, invitations)."""

from __future__ import annotations

import uuid
from datetime import UTC, datetime
from typing import Any

from fastapi import APIRouter, Depends, Request, Response
from pydantic import BaseModel, EmailStr, Field
from sqlalchemy import select
from sqlalchemy.orm import Session

from bankverse.auth import service
from bankverse.auth.deps import (
    ACCESS_COOKIE,
    CSRF_COOKIE,
    REFRESH_COOKIE,
    Principal,
    client_ip,
    control_db,
    get_principal,
)
from bankverse.auth.rbac import ROLE_LABELS
from bankverse.core.config import get_settings
from bankverse.core.errors import NotFound, Unauthorized, ValidationFailed
from bankverse.core.ratelimit import limiter
from bankverse.core.security import (
    PasswordPolicyError,
    hash_opaque_token,
    hash_password,
    validate_password_policy,
)
from bankverse.db.models.control import AuthSession, Invitation, Membership, Organization, User
from bankverse.services import audit

router = APIRouter(prefix="/auth", tags=["auth"])


class LoginIn(BaseModel):
    email: EmailStr
    password: str = Field(min_length=1, max_length=256)
    org_slug: str | None = Field(default=None, max_length=64)


class MfaVerifyIn(BaseModel):
    mfa_token: str
    code: str = Field(min_length=6, max_length=8)


class MfaSetupStartIn(BaseModel):
    setup_token: str | None = None


class MfaSetupConfirmIn(BaseModel):
    setup_token: str | None = None
    code: str = Field(min_length=6, max_length=8)


class SwitchOrgIn(BaseModel):
    org_id: uuid.UUID


class PasswordChangeIn(BaseModel):
    current_password: str
    new_password: str = Field(min_length=12, max_length=256)


class AcceptInvitationIn(BaseModel):
    token: str = Field(min_length=20, max_length=200)
    full_name: str = Field(min_length=2, max_length=200)
    password: str = Field(min_length=12, max_length=256)


def _set_cookies(response: Response, issued: service.IssuedSession) -> None:
    s = get_settings()
    common: dict[str, Any] = {"secure": s.cookie_secure, "samesite": "lax", "domain": s.cookie_domain}
    response.set_cookie(ACCESS_COOKIE, issued.access_token, max_age=s.access_token_ttl_seconds, httponly=True,
                        path="/", **common)
    response.set_cookie(REFRESH_COOKIE, issued.refresh_token, max_age=s.refresh_token_ttl_seconds, httponly=True,
                        path=f"{s.api_prefix}/auth", **common)
    response.set_cookie(CSRF_COOKIE, issued.csrf_token, max_age=s.refresh_token_ttl_seconds, httponly=False,
                        path="/", **common)


def _clear_cookies(response: Response) -> None:
    s = get_settings()
    response.delete_cookie(ACCESS_COOKIE, path="/", domain=s.cookie_domain)
    response.delete_cookie(REFRESH_COOKIE, path=f"{s.api_prefix}/auth", domain=s.cookie_domain)
    response.delete_cookie(CSRF_COOKIE, path="/", domain=s.cookie_domain)


def _session_payload(issued: service.IssuedSession) -> dict[str, Any]:
    return {
        "status": "ok",
        "user": {"id": str(issued.user.id), "email": issued.user.email, "full_name": issued.user.full_name},
        "org": {"id": str(issued.org.id), "slug": issued.org.slug, "name": issued.org.name, "kind": issued.org.kind},
        "role": issued.role,
        "csrf_token": issued.csrf_token,
    }


@router.post("/login")
def login(body: LoginIn, request: Request, response: Response, db: Session = Depends(control_db)) -> dict[str, Any]:
    ip = client_ip(request)
    limiter.hit(f"login:ip:{ip}", get_settings().rate_limit_auth_per_minute)
    limiter.hit(f"login:email:{body.email.lower()}", get_settings().rate_limit_auth_per_minute)
    result = service.login(db, email=body.email, password=body.password, org_slug=body.org_slug, ip=ip,
                           user_agent=request.headers.get("user-agent"))
    if isinstance(result, dict):
        return result
    _set_cookies(response, result)
    return _session_payload(result)


DEMO_SESSION_ROLES = {"fraud_analyst", "aml_analyst", "credit_analyst", "risk_manager", "data_scientist", "auditor"}


class DemoSessionIn(BaseModel):
    role: str


@router.post("/demo-session")
def demo_session(body: DemoSessionIn, request: Request, response: Response,
                 db: Session = Depends(control_db)) -> dict[str, Any]:
    """Opens a session on the public DEMO tenant for a non-administrative role (no MFA roles)."""
    s = get_settings()
    ip = client_ip(request)
    limiter.hit(f"demo:ip:{ip}", 20)
    if not s.public_demo_enabled:
        raise NotFound("Public demo is disabled in this environment")
    if body.role not in DEMO_SESSION_ROLES or body.role in s.mfa_required_roles:
        raise ValidationFailed("This role is not available for demo sessions")
    org = db.execute(select(Organization).where(Organization.slug == s.public_demo_org_slug,
                                                Organization.kind == "demo", Organization.status == "active")).scalar_one_or_none()
    if org is None:
        raise NotFound("Demo organisation not provisioned")
    row = db.execute(select(User, Membership).join(Membership, Membership.user_id == User.id)
                     .where(Membership.org_id == org.id, Membership.role == body.role, Membership.status == "active",
                            User.status == "active").order_by(User.created_at).limit(1)).first()
    if row is None:
        raise NotFound("No demo user for this role")
    user, membership = row
    issued = service.issue_session(db, user, org, membership.role, mfa=False, ip=ip,
                                   user_agent=request.headers.get("user-agent"))
    audit.record("auth.demo_session", org_id=org.id, actor_user_id=user.id, actor_label=user.email, ip=ip,
                 resource_type="session", resource_id=str(issued.session.id), details={"role": body.role}, session=db)
    _set_cookies(response, issued)
    return _session_payload(issued)


@router.post("/mfa/verify")
def mfa_verify(body: MfaVerifyIn, request: Request, response: Response, db: Session = Depends(control_db)) -> dict[str, Any]:
    ip = client_ip(request)
    limiter.hit(f"mfa:ip:{ip}", get_settings().rate_limit_auth_per_minute)
    issued = service.verify_mfa(db, mfa_token=body.mfa_token, code=body.code, ip=ip,
                                user_agent=request.headers.get("user-agent"))
    _set_cookies(response, issued)
    return _session_payload(issued)


@router.post("/mfa/setup/start")
def mfa_setup_start(body: MfaSetupStartIn, request: Request, db: Session = Depends(control_db)) -> dict[str, str]:
    limiter.hit(f"mfa-setup:ip:{client_ip(request)}", get_settings().rate_limit_auth_per_minute)
    if body.setup_token:
        return service.start_mfa_setup_with_token(db, body.setup_token)
    principal = get_principal(request)
    if principal.user_id is None:
        raise Unauthorized("User session required")
    user = db.get(User, principal.user_id)
    assert user is not None
    return service.start_mfa_setup(db, user)


@router.post("/mfa/setup/confirm")
def mfa_setup_confirm(body: MfaSetupConfirmIn, request: Request, response: Response,
                      db: Session = Depends(control_db)) -> dict[str, Any]:
    ip = client_ip(request)
    limiter.hit(f"mfa-setup:ip:{ip}", get_settings().rate_limit_auth_per_minute)
    if body.setup_token:
        issued = service.confirm_mfa_setup_with_token(db, setup_token=body.setup_token, code=body.code, ip=ip,
                                                      user_agent=request.headers.get("user-agent"))
        _set_cookies(response, issued)
        return _session_payload(issued)
    principal = get_principal(request)
    user = db.get(User, principal.user_id) if principal.user_id else None
    if user is None:
        raise Unauthorized("User session required")
    service.confirm_mfa_setup(db, user, body.code)
    return {"status": "ok"}


@router.post("/refresh")
def refresh(request: Request, response: Response, db: Session = Depends(control_db)) -> dict[str, Any]:
    token = request.cookies.get(REFRESH_COOKIE)
    if not token:
        raise Unauthorized("No refresh token", code="session_invalid")
    limiter.hit(f"refresh:ip:{client_ip(request)}", 60)
    issued = service.refresh(db, refresh_token=token, ip=client_ip(request))
    _set_cookies(response, issued)
    return _session_payload(issued)


@router.post("/logout")
def logout(request: Request, response: Response, db: Session = Depends(control_db)) -> dict[str, str]:
    token = request.cookies.get(REFRESH_COOKIE)
    if token:
        sess = db.execute(select(AuthSession).where(AuthSession.refresh_hash == hash_opaque_token(token))).scalar_one_or_none()
        if sess:
            service.revoke_session(db, sess.id, "logout")
            audit.record("auth.logout", org_id=sess.org_id, actor_user_id=sess.user_id, ip=client_ip(request), session=db)
    _clear_cookies(response)
    return {"status": "ok"}


@router.get("/me")
def me(principal: Principal = Depends(get_principal), db: Session = Depends(control_db)) -> dict[str, Any]:
    memberships = []
    if principal.user_id:
        rows = db.execute(
            select(Membership, Organization)
            .join(Organization, Organization.id == Membership.org_id)
            .where(Membership.user_id == principal.user_id, Membership.status == "active",
                   Organization.status == "active")
            .order_by(Organization.name)
        ).all()
        memberships = [
            {"org_id": str(o.id), "slug": o.slug, "name": o.name, "kind": o.kind, "role": m.role}
            for m, o in rows
        ]
    user = db.get(User, principal.user_id) if principal.user_id else None
    return {
        "kind": principal.kind,
        "user": None if user is None else {
            "id": str(user.id), "email": user.email, "full_name": user.full_name, "locale": user.locale,
            "mfa_enabled": user.mfa_enabled, "is_platform_admin": user.is_platform_admin,
        },
        "org": {"id": str(principal.org_id), "slug": principal.org_slug, "name": principal.org_name,
                "kind": principal.org_kind, "isolation_mode": principal.isolation_mode},
        "role": principal.role,
        "role_label": ROLE_LABELS.get(principal.role, {}),
        "permissions": sorted(principal.permissions),
        "modules": sorted(principal.enabled_modules),
        "mfa_verified": principal.mfa_verified,
        "memberships": memberships,
    }


@router.post("/switch-org")
def switch_org(body: SwitchOrgIn, request: Request, response: Response,
               principal: Principal = Depends(get_principal), db: Session = Depends(control_db)) -> dict[str, Any]:
    if principal.session_id is None or principal.user_id is None:
        raise Unauthorized("User session required")
    issued = service.switch_org(db, session_id=principal.session_id, user_id=principal.user_id, org_id=body.org_id)
    _set_cookies(response, issued)
    return _session_payload(issued)


@router.get("/sessions")
def list_sessions(principal: Principal = Depends(get_principal), db: Session = Depends(control_db)) -> list[dict[str, Any]]:
    rows = db.execute(
        select(AuthSession).where(AuthSession.user_id == principal.user_id, AuthSession.revoked_at.is_(None),
                                  AuthSession.expires_at > datetime.now(UTC))
        .order_by(AuthSession.created_at.desc())
    ).scalars()
    return [
        {"id": str(s.id), "created_at": s.created_at, "last_seen_at": s.last_seen_at, "ip": s.ip,
         "user_agent": s.user_agent, "current": s.id == principal.session_id, "mfa_verified": s.mfa_verified}
        for s in rows
    ]


@router.delete("/sessions/{session_id}")
def revoke(session_id: uuid.UUID, principal: Principal = Depends(get_principal),
           db: Session = Depends(control_db)) -> dict[str, str]:
    sess = db.get(AuthSession, session_id)
    if sess is None or sess.user_id != principal.user_id:
        raise NotFound("Session not found")
    service.revoke_session(db, session_id, "user_revoked")
    audit.record("auth.session_revoked", org_id=principal.org_id, actor_user_id=principal.user_id,
                 resource_type="session", resource_id=str(session_id), session=db)
    return {"status": "ok"}


@router.post("/password")
def change_password(body: PasswordChangeIn, principal: Principal = Depends(get_principal),
                    db: Session = Depends(control_db)) -> dict[str, str]:
    user = db.get(User, principal.user_id) if principal.user_id else None
    if user is None:
        raise Unauthorized("User session required")
    try:
        service.change_password(db, user, body.current_password, body.new_password)
    except PasswordPolicyError as exc:
        raise ValidationFailed(str(exc), code="password_policy") from exc
    return {"status": "ok"}


@router.get("/invitations/{token}")
def invitation_info(token: str, request: Request, db: Session = Depends(control_db)) -> dict[str, Any]:
    limiter.hit(f"invite:ip:{client_ip(request)}", 30)
    inv = db.execute(select(Invitation).where(Invitation.token_hash == hash_opaque_token(token))).scalar_one_or_none()
    if inv is None or inv.accepted_at or inv.revoked_at or inv.expires_at < datetime.now(UTC):
        raise NotFound("Invitation not found or expired")
    org = db.get(Organization, inv.org_id)
    return {"email": inv.email, "role": inv.role, "org_name": org.name if org else None,
            "existing_account": db.execute(select(User.id).where(User.email == inv.email)).first() is not None}


@router.post("/invitations/accept")
def accept_invitation(body: AcceptInvitationIn, request: Request, db: Session = Depends(control_db)) -> dict[str, str]:
    limiter.hit(f"invite:ip:{client_ip(request)}", 30)
    inv = db.execute(select(Invitation).where(Invitation.token_hash == hash_opaque_token(body.token))).scalar_one_or_none()
    if inv is None or inv.accepted_at or inv.revoked_at or inv.expires_at < datetime.now(UTC):
        raise NotFound("Invitation not found or expired")
    user = db.execute(select(User).where(User.email == inv.email)).scalar_one_or_none()
    if user is None:
        try:
            validate_password_policy(body.password)
        except PasswordPolicyError as exc:
            raise ValidationFailed(str(exc), code="password_policy") from exc
        user = User(email=inv.email, full_name=body.full_name, password_hash=hash_password(body.password),
                    password_changed_at=datetime.now(UTC))
        db.add(user)
        db.flush()
    exists = db.execute(select(Membership).where(Membership.user_id == user.id, Membership.org_id == inv.org_id)).scalar_one_or_none()
    if exists is None:
        db.add(Membership(user_id=user.id, org_id=inv.org_id, role=inv.role))
    inv.accepted_at = datetime.now(UTC)
    audit.record("org.invitation_accepted", org_id=inv.org_id, actor_user_id=user.id, actor_label=user.email,
                 resource_type="invitation", resource_id=str(inv.id), ip=client_ip(request), session=db)
    return {"status": "ok"}
