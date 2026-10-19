"""Declarative bases.

Two metadata collections keep the *control plane* separate from *tenant business data*:

* ``ControlBase`` → schemas ``control`` (identity, organisations, audit) and ``platform``
  (data catalogue, pipelines, model registry, public reference data). Always lives in the shared
  platform database.
* ``TenantBase`` → schema ``bank`` (customers, transactions, alerts, cases, documents...). Lives in
  the shared database for DEMO/standard tenants (protected by FORCED row-level security), or in a
  dedicated database per bank for pilots (``organizations.isolation_mode = 'dedicated'``).
"""

from __future__ import annotations

import uuid
from datetime import UTC, datetime

from sqlalchemy import DateTime, MetaData, func
from sqlalchemy.dialects.postgresql import UUID
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column

NAMING = {
    "ix": "ix_%(column_0_label)s",
    "uq": "uq_%(table_name)s_%(column_0_name)s",
    "ck": "ck_%(table_name)s_%(constraint_name)s",
    "fk": "fk_%(table_name)s_%(column_0_name)s_%(referred_table_name)s",
    "pk": "pk_%(table_name)s",
}


def utcnow() -> datetime:
    return datetime.now(UTC)


class ControlBase(DeclarativeBase):
    metadata = MetaData(naming_convention=NAMING)


class TenantBase(DeclarativeBase):
    metadata = MetaData(naming_convention=NAMING, schema="bank")


class UUIDPk:
    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)


class Timestamped:
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), default=utcnow, nullable=False
    )


class TenantScoped:
    """Every tenant row carries its organisation id; RLS policies filter on it."""

    org_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), nullable=False, index=True)
