"""Authentication use-cases: password login, TOTP MFA, sessions, refresh rotation, org switching."""

from __future__ import annotations

import uuid
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta

from sqlalchemy import select
from sqlalchemy.orm import Session

from bankverse.core.config import get_settings
from bankverse.core.errors import Forbidden, Unauthorized, ValidationFailed
from bankverse.core.security import (
    InvalidTokenError,
    create_token,
    decode_token,
    decrypt_field,
    dummy_verify,
    encrypt_field,
    hash_opaque_token,
    hash_password,
    needs_rehash,
    new_opaque_token,
    new_totp_secret,
    totp_provisioning_uri,
    totp_qr_svg,
    validate_password_policy,
    verify_password,
    verify_totp,
)
from bankverse.db.models.control import AuthSession, Membership, Organization, User
from bankverse.services import audit, security_events


@dataclass
class IssuedSession:
    access_token: str
    refresh_token: str
    csrf_token: str
    session: AuthSession
    user: User
    org: Organization
    role: str


def _requires_mfa(role: str) -> bool:
    s = get_settings()
    return s.enforce_mfa and role in s.mfa_required_roles


def _pick_membership(db: Session, user: User, org_slug: str | None) -> tuple[Membership, Organization]:
    q = (
        select(Membership, Organization)
        .join(Organization, Organization.id == Membership.org_id)
        .where(Membership.user_id == user.id, Membership.status == "active", Organization.status == "active")
        .order_by(Membership.created_at.asc())
    )
    if org_slug:
        q = q.where(Organization.slug == org_slug)
    row = db.execute(q).first()
    if row is None:
        raise Forbidden("No active organisation membership", code="no_membership")
    return row[0], row[1]


def issue_session(
    db: Session, user: User, org: Organization, role: str, *, mfa: bool, ip: str | None, user_agent: str | None
) -> IssuedSession:
    s = get_settings()
    refresh = new_opaque_token()
    sess = AuthSession(
        user_id=user.id,
        org_id=org.id,
        refresh_hash=hash_opaque_token(refresh),
        mfa_verified=mfa,
        ip=ip,
        user_agent=(user_agent or "")[:400],
        expires_at=datetime.now(UTC) + timedelta(seconds=s.refresh_token_ttl_seconds),
        last_seen_at=datetime.now(UTC),
    )
    db.add(sess)
    db.flush()
    access = create_token(
        subject=str(user.id), purpose="access", ttl_seconds=s.access_token_ttl_seconds,
        session_id=str(sess.id), org_id=str(org.id), mfa=mfa,
    )
    return IssuedSession(access, refresh, new_opaque_token(24), sess, user, org, role)


def login(
    db: Session, *, email: str, password: str, org_slug: str | None, ip: str | None, user_agent: str | None
) -> dict[str, object] | IssuedSession:
    s = get_settings()
    email = email.strip().lower()
    user = db.execute(select(User).where(User.email == email)).scalar_one_or_none()
    now = datetime.now(UTC)
    if user is None or not user.password_hash:
        dummy_verify()
        audit.record("auth.login", outcome="failure", ip=ip, actor_label=email, details={"reason": "unknown_user"},
                     session=db)
        raise Unauthorized("Invalid credentials", code="invalid_credentials")
    if user.status != "active" or (user.locked_until and user.locked_until > now):
        dummy_verify()
        audit.record("auth.login", outcome="failure", ip=ip, actor_user_id=user.id, actor_label=email,
                     details={"reason": "locked_or_disabled"}, session=db)
        raise Unauthorized("Invalid credentials", code="invalid_credentials")
    if not verify_password(user.password_hash, password):
        user.failed_logins += 1
        details = {"reason": "bad_password", "failed_logins": user.failed_logins}
        if user.failed_logins >= s.max_failed_logins:
            user.locked_until = now + timedelta(minutes=s.lockout_minutes)
            user.failed_logins = 0
            security_events.emit("brute_force", severity="high", user_id=user.id, ip=ip,
                                 details={"locked_minutes": s.lockout_minutes}, db=db)
            details["locked"] = True
        audit.record("auth.login", outcome="failure", ip=ip, actor_user_id=user.id, actor_label=email,
                     details=details, session=db)
        db.commit()
        raise Unauthorized("Invalid credentials", code="invalid_credentials")

    if needs_rehash(user.password_hash):
        user.password_hash = hash_password(password)
    user.failed_logins = 0
    membership, org = _pick_membership(db, user, org_slug)

    if user.mfa_enabled:
        token = create_token(subject=str(user.id), purpose="mfa", ttl_seconds=s.mfa_token_ttl_seconds,
                             org_id=str(org.id))
        return {"status": "mfa_required", "mfa_token": token}
    if _requires_mfa(membership.role):
        token = create_token(subject=str(user.id), purpose="mfa_setup", ttl_seconds=15 * 60, org_id=str(org.id))
        return {"status": "mfa_setup_required", "setup_token": token}
    return _complete_login(db, user, org, membership.role, mfa=False, ip=ip, user_agent=user_agent)


def _complete_login(
    db: Session, user: User, org: Organization, role: str, *, mfa: bool, ip: str | None, user_agent: str | None
) -> IssuedSession:
    if security_events.check_new_ip(db, user.id, ip):
        security_events.emit("new_ip_login", severity="low", org_id=org.id, user_id=user.id, ip=ip, db=db)
    user.last_login_at = datetime.now(UTC)
    user.last_login_ip = ip
    issued = issue_session(db, user, org, role, mfa=mfa, ip=ip, user_agent=user_agent)
    audit.record("auth.login", org_id=org.id, actor_user_id=user.id, actor_label=user.email, ip=ip,
                 resource_type="session", resource_id=str(issued.session.id), details={"mfa": mfa}, session=db)
    return issued


def _user_from_token(db: Session, token: str, purpose: str) -> tuple[User, uuid.UUID]:
    try:
        claims = decode_token(token, purpose)  # type: ignore[arg-type]
    except InvalidTokenError as exc:
        raise Unauthorized("Verification step expired, please sign in again", code="token_invalid") from exc
    user = db.get(User, uuid.UUID(claims.sub))
    if user is None or user.status != "active" or not claims.org:
        raise Unauthorized("Invalid verification token", code="token_invalid")
    return user, uuid.UUID(claims.org)


def verify_mfa(db: Session, *, mfa_token: str, code: str, ip: str | None, user_agent: str | None) -> IssuedSession:
    user, org_id = _user_from_token(db, mfa_token, "mfa")
    if not user.mfa_secret_enc or not verify_totp(decrypt_field(user.mfa_secret_enc, aad=str(user.id)), code):
        audit.record("auth.mfa", outcome="failure", ip=ip, actor_user_id=user.id, actor_label=user.email, session=db)
        db.commit()
        raise Unauthorized("Invalid verification code", code="mfa_invalid")
    membership = db.execute(
        select(Membership).where(Membership.user_id == user.id, Membership.org_id == org_id, Membership.status == "active")
    ).scalar_one_or_none()
    org = db.get(Organization, org_id)
    if membership is None or org is None:
        raise Forbidden("No active organisation membership", code="no_membership")
    return _complete_login(db, user, org, membership.role, mfa=True, ip=ip, user_agent=user_agent)


def start_mfa_setup(db: Session, user: User) -> dict[str, str]:
    secret = new_totp_secret()
    user.mfa_secret_enc = encrypt_field(secret, aad=str(user.id))
    user.mfa_enabled = False
    uri = totp_provisioning_uri(secret, user.email)
    return {"secret": secret, "otpauth_uri": uri, "qr_svg": totp_qr_svg(uri)}


def start_mfa_setup_with_token(db: Session, setup_token: str) -> dict[str, str]:
    user, _ = _user_from_token(db, setup_token, "mfa_setup")
    return start_mfa_setup(db, user)


def confirm_mfa_setup(db: Session, user: User, code: str) -> None:
    if not user.mfa_secret_enc or not verify_totp(decrypt_field(user.mfa_secret_enc, aad=str(user.id)), code):
        raise ValidationFailed("Invalid verification code", code="mfa_invalid")
    user.mfa_enabled = True
    audit.record("auth.mfa_enabled", actor_user_id=user.id, actor_label=user.email, session=db)


def confirm_mfa_setup_with_token(
    db: Session, *, setup_token: str, code: str, ip: str | None, user_agent: str | None
) -> IssuedSession:
    user, org_id = _user_from_token(db, setup_token, "mfa_setup")
    confirm_mfa_setup(db, user, code)
    membership = db.execute(
        select(Membership).where(Membership.user_id == user.id, Membership.org_id == org_id)
    ).scalar_one()
    org = db.get(Organization, org_id)
    assert org is not None
    return _complete_login(db, user, org, membership.role, mfa=True, ip=ip, user_agent=user_agent)


def refresh(db: Session, *, refresh_token: str, ip: str | None) -> IssuedSession:
    s = get_settings()
    h = hash_opaque_token(refresh_token)
    sess = db.execute(select(AuthSession).where(AuthSession.refresh_hash == h)).scalar_one_or_none()
    now = datetime.now(UTC)
    if sess is None or sess.revoked_at is not None or sess.expires_at < now:
        raise Unauthorized("Session expired", code="session_invalid")
    user = db.get(User, sess.user_id)
    org = db.get(Organization, sess.org_id) if sess.org_id else None
    if user is None or user.status != "active" or org is None or org.status != "active":
        raise Unauthorized("Session expired", code="session_invalid")
    membership = db.execute(
        select(Membership).where(Membership.user_id == user.id, Membership.org_id == org.id, Membership.status == "active")
    ).scalar_one_or_none()
    if membership is None:
        sess.revoked_at = now
        sess.revoke_reason = "membership_removed"
        raise Unauthorized("Session expired", code="session_invalid")
    # Rotation: a new refresh token replaces the old one (old one becomes useless).
    new_refresh = new_opaque_token()
    sess.refresh_hash = hash_opaque_token(new_refresh)
    sess.last_seen_at = now
    access = create_token(subject=str(user.id), purpose="access", ttl_seconds=s.access_token_ttl_seconds,
                          session_id=str(sess.id), org_id=str(org.id), mfa=sess.mfa_verified)
    return IssuedSession(access, new_refresh, new_opaque_token(24), sess, user, org, membership.role)


def switch_org(db: Session, *, session_id: uuid.UUID, user_id: uuid.UUID, org_id: uuid.UUID) -> IssuedSession:
    s = get_settings()
    sess = db.get(AuthSession, session_id)
    if sess is None or sess.user_id != user_id or sess.revoked_at is not None:
        raise Unauthorized("Session expired", code="session_invalid")
    membership = db.execute(
        select(Membership).where(Membership.user_id == user_id, Membership.org_id == org_id, Membership.status == "active")
    ).scalar_one_or_none()
    org = db.get(Organization, org_id)
    if membership is None or org is None or org.status != "active":
        raise Forbidden("No active membership in this organisation", code="no_membership")
    if _requires_mfa(membership.role) and not sess.mfa_verified:
        raise Forbidden("This role requires multi-factor authentication", code="mfa_required")
    user = db.get(User, user_id)
    assert user is not None
    new_refresh = new_opaque_token()
    sess.refresh_hash = hash_opaque_token(new_refresh)
    sess.org_id = org.id
    access = create_token(subject=str(user.id), purpose="access", ttl_seconds=s.access_token_ttl_seconds,
                          session_id=str(sess.id), org_id=str(org.id), mfa=sess.mfa_verified)
    audit.record("auth.switch_org", org_id=org.id, actor_user_id=user.id, actor_label=user.email, session=db)
    return IssuedSession(access, new_refresh, new_opaque_token(24), sess, user, org, membership.role)


def revoke_session(db: Session, session_id: uuid.UUID, reason: str) -> None:
    sess = db.get(AuthSession, session_id)
    if sess and sess.revoked_at is None:
        sess.revoked_at = datetime.now(UTC)
        sess.revoke_reason = reason


def change_password(db: Session, user: User, current: str, new: str) -> None:
    if not user.password_hash or not verify_password(user.password_hash, current):
        raise Unauthorized("Current password is incorrect", code="invalid_credentials")
    validate_password_policy(new)
    user.password_hash = hash_password(new)
    user.password_changed_at = datetime.now(UTC)
    audit.record("auth.password_changed", actor_user_id=user.id, actor_label=user.email, session=db)
