"""Detection of anomalous access patterns (stored as security events + audit entries).

Heuristics implemented (deliberately simple and explainable):
* ``brute_force``       — N failed logins on an account within 15 minutes (also triggers lockout).
* ``new_ip_login``      — successful login from an IP never seen for this user in the last 90 days.
* ``cross_tenant_probe``— a principal requests resources of another organisation (404/403 on IDs
  that exist in another tenant) more than 3 times in 10 minutes.
* ``mass_export``       — more than 20 report/export downloads by one user within 10 minutes.
"""

from __future__ import annotations

import uuid
from datetime import UTC, datetime, timedelta
from typing import Any

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from bankverse.db.models.control import AuditLog, SecurityEvent
from bankverse.db.session import control_session


def emit(
    kind: str,
    *,
    severity: str = "low",
    org_id: uuid.UUID | None = None,
    user_id: uuid.UUID | None = None,
    ip: str | None = None,
    details: dict[str, Any] | None = None,
    db: Session | None = None,
) -> None:
    ev = SecurityEvent(kind=kind, severity=severity, org_id=org_id, user_id=user_id, ip=ip, details=details or {})
    if db is not None:
        db.add(ev)
        db.flush()
    else:
        with control_session() as s:
            s.add(ev)


def check_new_ip(db: Session, user_id: uuid.UUID, ip: str | None) -> bool:
    if not ip:
        return False
    since = datetime.now(UTC) - timedelta(days=90)
    seen = db.execute(
        select(func.count())
        .select_from(AuditLog)
        .where(
            AuditLog.actor_user_id == user_id,
            AuditLog.action == "auth.login",
            AuditLog.outcome == "success",
            AuditLog.ip == ip,
            AuditLog.created_at >= since,
        )
    ).scalar_one()
    prior_logins = db.execute(
        select(func.count())
        .select_from(AuditLog)
        .where(AuditLog.actor_user_id == user_id, AuditLog.action == "auth.login", AuditLog.outcome == "success")
    ).scalar_one()
    return prior_logins > 0 and seen == 0


def count_recent(db: Session, *, action: str, user_id: uuid.UUID | None, minutes: int, outcome: str | None = None) -> int:
    since = datetime.now(UTC) - timedelta(minutes=minutes)
    q = select(func.count()).select_from(AuditLog).where(AuditLog.action == action, AuditLog.created_at >= since)
    if user_id is not None:
        q = q.where(AuditLog.actor_user_id == user_id)
    if outcome:
        q = q.where(AuditLog.outcome == outcome)
    return int(db.execute(q).scalar_one())
