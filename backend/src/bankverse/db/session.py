"""Engines, sessions and tenant routing.

* The API runs with the least-privileged ``bankverse_app`` role (NOBYPASSRLS).
* Each tenant session sets ``app.org_id`` *transaction-locally* at every BEGIN, so that the
  PostgreSQL row-level-security policies filter every statement — even if application code forgot a
  ``WHERE org_id = ...`` clause (defence in depth).
* Organisations in ``dedicated`` isolation mode are routed to their own database, whose URL is read
  from a secret (environment variable locally, AWS Secrets Manager in the cloud).
"""

from __future__ import annotations

import os
import threading
import uuid
from collections.abc import Iterator
from contextlib import contextmanager
from dataclasses import dataclass

from sqlalchemy import Engine, create_engine, event, text
from sqlalchemy.orm import Session, sessionmaker

from bankverse.core.config import get_settings

_engines: dict[str, Engine] = {}
_lock = threading.Lock()


def _make_engine(url: str) -> Engine:
    s = get_settings()
    return create_engine(
        url,
        pool_size=s.db_pool_size,
        max_overflow=s.db_max_overflow,
        pool_pre_ping=True,
        pool_recycle=1800,
        echo=s.db_echo,
        connect_args={"application_name": "bankverse", "options": "-c statement_timeout=60000"},
    )


def _engine(url: str) -> Engine:
    eng = _engines.get(url)
    if eng is None:
        with _lock:
            eng = _engines.get(url)
            if eng is None:
                eng = _make_engine(url)
                _engines[url] = eng
    return eng


def app_engine() -> Engine:
    return _engine(get_settings().app_database_url)


def owner_engine() -> Engine:
    return _engine(get_settings().database_url)


def dispose_engines() -> None:
    with _lock:
        for eng in _engines.values():
            eng.dispose()
        _engines.clear()


@dataclass(frozen=True)
class TenantTarget:
    org_id: uuid.UUID
    isolation_mode: str = "shared"
    dedicated_db_secret: str | None = None


def resolve_secret(name: str) -> str:
    """Resolve a secret by name: env var first, then AWS Secrets Manager (cloud environments)."""
    env_name = name.upper().replace("/", "_").replace("-", "_")
    if env_name in os.environ:
        return os.environ[env_name]
    s = get_settings()
    if s.env in ("staging", "pilot"):
        import boto3  # local import: only needed in AWS

        client = boto3.client("secretsmanager", region_name=s.aws_region)
        return client.get_secret_value(SecretId=name)["SecretString"]
    raise KeyError(f"Secret {name!r} not found (expected env var {env_name})")


def tenant_engine(target: TenantTarget, *, owner: bool = False) -> Engine:
    if target.isolation_mode == "dedicated":
        if not target.dedicated_db_secret:
            raise RuntimeError("Dedicated organisation without database secret")
        return _engine(resolve_secret(target.dedicated_db_secret))
    return owner_engine() if owner else app_engine()


_ControlSession = sessionmaker(expire_on_commit=False, autoflush=False)


@contextmanager
def control_session(*, owner: bool = False) -> Iterator[Session]:
    """Session for the control plane / platform schemas (no tenant data)."""
    session = _ControlSession(bind=owner_engine() if owner else app_engine())
    try:
        yield session
        session.commit()
    except Exception:
        session.rollback()
        raise
    finally:
        session.close()


def _bind_org(session: Session, org_id: uuid.UUID) -> None:
    org_str = str(org_id)

    @event.listens_for(session, "after_begin")
    def _set_org(sess: Session, transaction: object, connection: object) -> None:
        connection.execute(text("SELECT set_config('app.org_id', :org, true)"), {"org": org_str})  # type: ignore[attr-defined]


def open_tenant_session(target: TenantTarget, *, owner: bool = False) -> Session:
    session = Session(bind=tenant_engine(target, owner=owner), expire_on_commit=False, autoflush=False)
    _bind_org(session, target.org_id)
    session.info["org_id"] = target.org_id
    return session


@contextmanager
def tenant_session(target: TenantTarget, *, owner: bool = False) -> Iterator[Session]:
    session = open_tenant_session(target, owner=owner)
    try:
        yield session
        session.commit()
    except Exception:
        session.rollback()
        raise
    finally:
        session.close()
