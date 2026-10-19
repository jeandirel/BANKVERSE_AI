"""Control-plane models (schema ``control``): identity, organisations, access, quotas, audit."""

from __future__ import annotations

import uuid
from datetime import datetime
from typing import Any

from sqlalchemy import (
    BigInteger,
    Boolean,
    DateTime,
    ForeignKey,
    Integer,
    String,
    Text,
    UniqueConstraint,
)
from sqlalchemy.dialects.postgresql import JSONB, UUID
from sqlalchemy.orm import Mapped, mapped_column, relationship

from bankverse.db.base import ControlBase, Timestamped, UUIDPk

SCHEMA = {"schema": "control"}


class Organization(UUIDPk, Timestamped, ControlBase):
    __tablename__ = "organizations"
    __table_args__ = SCHEMA

    slug: Mapped[str] = mapped_column(String(64), unique=True, nullable=False)
    name: Mapped[str] = mapped_column(String(200), nullable=False)
    kind: Mapped[str] = mapped_column(String(16), nullable=False, default="bank")  # demo | bank | platform
    country: Mapped[str] = mapped_column(String(2), nullable=False, default="FR")
    status: Mapped[str] = mapped_column(String(16), nullable=False, default="active")  # active|suspended|deprovisioned
    plan: Mapped[str] = mapped_column(String(32), nullable=False, default="pilot")
    isolation_mode: Mapped[str] = mapped_column(String(16), nullable=False, default="shared")  # shared|dedicated
    # Name of the secret holding the dedicated database URL (env var locally, Secrets Manager in AWS).
    dedicated_db_secret: Mapped[str | None] = mapped_column(String(200))
    data_region: Mapped[str] = mapped_column(String(32), nullable=False, default="eu-west-3")
    settings: Mapped[dict[str, Any]] = mapped_column(JSONB, nullable=False, default=dict)
    suspended_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    deprovisioned_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))

    modules: Mapped[list[OrgModule]] = relationship(back_populates="org", cascade="all, delete-orphan")


class User(UUIDPk, Timestamped, ControlBase):
    __tablename__ = "users"
    __table_args__ = SCHEMA

    email: Mapped[str] = mapped_column(String(320), unique=True, nullable=False, index=True)
    full_name: Mapped[str] = mapped_column(String(200), nullable=False)
    password_hash: Mapped[str | None] = mapped_column(Text)
    locale: Mapped[str] = mapped_column(String(5), nullable=False, default="fr")
    status: Mapped[str] = mapped_column(String(16), nullable=False, default="active")  # active|disabled
    is_platform_admin: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)
    mfa_enabled: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)
    mfa_secret_enc: Mapped[str | None] = mapped_column(Text)  # AES-GCM encrypted TOTP secret
    failed_logins: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    locked_until: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    last_login_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    last_login_ip: Mapped[str | None] = mapped_column(String(64))
    password_changed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))

    memberships: Mapped[list[Membership]] = relationship(back_populates="user", cascade="all, delete-orphan")


class Membership(UUIDPk, Timestamped, ControlBase):
    __tablename__ = "memberships"
    __table_args__ = (UniqueConstraint("user_id", "org_id", name="uq_membership_user_org"), SCHEMA)

    user_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("control.users.id", ondelete="CASCADE"), index=True)
    org_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("control.organizations.id", ondelete="CASCADE"), index=True
    )
    role: Mapped[str] = mapped_column(String(40), nullable=False)
    status: Mapped[str] = mapped_column(String(16), nullable=False, default="active")  # active|suspended

    user: Mapped[User] = relationship(back_populates="memberships")
    org: Mapped[Organization] = relationship()


class Invitation(UUIDPk, Timestamped, ControlBase):
    __tablename__ = "invitations"
    __table_args__ = SCHEMA

    org_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("control.organizations.id", ondelete="CASCADE"), index=True)
    email: Mapped[str] = mapped_column(String(320), nullable=False)
    role: Mapped[str] = mapped_column(String(40), nullable=False)
    token_hash: Mapped[str] = mapped_column(String(64), nullable=False, unique=True)
    invited_by: Mapped[uuid.UUID | None] = mapped_column(ForeignKey("control.users.id", ondelete="SET NULL"))
    expires_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    accepted_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    revoked_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))


class ApiKey(UUIDPk, Timestamped, ControlBase):
    """Service identities for system-to-system integration (scoped to one organisation and one role)."""

    __tablename__ = "api_keys"
    __table_args__ = SCHEMA

    org_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("control.organizations.id", ondelete="CASCADE"), index=True)
    name: Mapped[str] = mapped_column(String(120), nullable=False)
    prefix: Mapped[str] = mapped_column(String(8), nullable=False, unique=True)
    key_hash: Mapped[str] = mapped_column(String(64), nullable=False)
    role: Mapped[str] = mapped_column(String(40), nullable=False)
    created_by: Mapped[uuid.UUID | None] = mapped_column(ForeignKey("control.users.id", ondelete="SET NULL"))
    expires_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    last_used_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    revoked_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))


class AuthSession(UUIDPk, Timestamped, ControlBase):
    __tablename__ = "auth_sessions"
    __table_args__ = SCHEMA

    user_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("control.users.id", ondelete="CASCADE"), index=True)
    org_id: Mapped[uuid.UUID | None] = mapped_column(ForeignKey("control.organizations.id", ondelete="CASCADE"))
    refresh_hash: Mapped[str] = mapped_column(String(64), nullable=False, unique=True)
    mfa_verified: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)
    ip: Mapped[str | None] = mapped_column(String(64))
    user_agent: Mapped[str | None] = mapped_column(String(400))
    expires_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    last_seen_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    revoked_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    revoke_reason: Mapped[str | None] = mapped_column(String(64))


class OrgModule(ControlBase):
    __tablename__ = "org_modules"
    __table_args__ = SCHEMA

    org_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("control.organizations.id", ondelete="CASCADE"), primary_key=True
    )
    module: Mapped[str] = mapped_column(String(32), primary_key=True)
    enabled: Mapped[bool] = mapped_column(Boolean, nullable=False, default=True)
    config: Mapped[dict[str, Any]] = mapped_column(JSONB, nullable=False, default=dict)

    org: Mapped[Organization] = relationship(back_populates="modules")


class OrgQuota(ControlBase):
    __tablename__ = "org_quotas"
    __table_args__ = SCHEMA

    org_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("control.organizations.id", ondelete="CASCADE"), primary_key=True
    )
    metric: Mapped[str] = mapped_column(String(48), primary_key=True)
    monthly_limit: Mapped[int] = mapped_column(BigInteger, nullable=False)


class UsageCounter(ControlBase):
    __tablename__ = "usage_counters"
    __table_args__ = SCHEMA

    org_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("control.organizations.id", ondelete="CASCADE"), primary_key=True
    )
    metric: Mapped[str] = mapped_column(String(48), primary_key=True)
    period: Mapped[str] = mapped_column(String(7), primary_key=True)  # YYYY-MM
    count: Mapped[int] = mapped_column(BigInteger, nullable=False, default=0)


class AuditLog(Timestamped, ControlBase):
    """Append-only, hash-chained audit trail (UPDATE/DELETE revoked from the runtime role)."""

    __tablename__ = "audit_log"
    __table_args__ = SCHEMA

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True, autoincrement=True)
    org_id: Mapped[uuid.UUID | None] = mapped_column(UUID(as_uuid=True), index=True)
    actor_user_id: Mapped[uuid.UUID | None] = mapped_column(UUID(as_uuid=True), index=True)
    actor_type: Mapped[str] = mapped_column(String(16), nullable=False, default="user")  # user|api_key|system|agent
    actor_label: Mapped[str | None] = mapped_column(String(320))
    action: Mapped[str] = mapped_column(String(80), nullable=False, index=True)
    resource_type: Mapped[str | None] = mapped_column(String(64))
    resource_id: Mapped[str | None] = mapped_column(String(80))
    outcome: Mapped[str] = mapped_column(String(16), nullable=False, default="success")
    ip: Mapped[str | None] = mapped_column(String(64))
    request_id: Mapped[str | None] = mapped_column(String(64))
    details: Mapped[dict[str, Any]] = mapped_column(JSONB, nullable=False, default=dict)
    prev_hash: Mapped[str | None] = mapped_column(String(64))
    hash: Mapped[str] = mapped_column(String(64), nullable=False)


class SecurityEvent(UUIDPk, Timestamped, ControlBase):
    """Signals for anomalous access detection (brute force, new device, cross-tenant probing...)."""

    __tablename__ = "security_events"
    __table_args__ = SCHEMA

    org_id: Mapped[uuid.UUID | None] = mapped_column(UUID(as_uuid=True), index=True)
    user_id: Mapped[uuid.UUID | None] = mapped_column(UUID(as_uuid=True), index=True)
    kind: Mapped[str] = mapped_column(String(48), nullable=False, index=True)
    severity: Mapped[str] = mapped_column(String(16), nullable=False, default="low")
    ip: Mapped[str | None] = mapped_column(String(64))
    details: Mapped[dict[str, Any]] = mapped_column(JSONB, nullable=False, default=dict)
    acknowledged_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))


class SsoConfig(UUIDPk, Timestamped, ControlBase):
    __tablename__ = "sso_configs"
    __table_args__ = SCHEMA

    org_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("control.organizations.id", ondelete="CASCADE"), unique=True
    )
    issuer: Mapped[str] = mapped_column(String(400), nullable=False)
    client_id: Mapped[str] = mapped_column(String(200), nullable=False)
    client_secret_enc: Mapped[str | None] = mapped_column(Text)
    default_role: Mapped[str] = mapped_column(String(40), nullable=False, default="auditor")
    enabled: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)

