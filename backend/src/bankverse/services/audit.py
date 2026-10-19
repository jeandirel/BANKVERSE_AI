"""Tamper-evident audit trail.

Each entry stores ``hash = sha256(prev_hash || canonical_json(entry))``. Inserts are serialised
with a transaction-scoped advisory lock so the chain stays linear. ``verify_chain`` recomputes the
whole chain and reports the first broken link (used by the governance audit report).
"""

from __future__ import annotations

import hashlib
import uuid
from datetime import UTC, datetime
from typing import Any

import orjson
from sqlalchemy import select, text
from sqlalchemy.orm import Session

from bankverse.core.logging import get_logger, request_id_var
from bankverse.db.models.control import AuditLog
from bankverse.db.session import control_session

log = get_logger(__name__)
_CHAIN_LOCK = 774_211_001


def _canonical(entry: dict[str, Any]) -> bytes:
    return orjson.dumps(entry, option=orjson.OPT_SORT_KEYS, default=str)


def _entry_payload(row: AuditLog) -> dict[str, Any]:
    return {
        "org_id": str(row.org_id) if row.org_id else None,
        "actor_user_id": str(row.actor_user_id) if row.actor_user_id else None,
        "actor_type": row.actor_type,
        "actor_label": row.actor_label,
        "action": row.action,
        "resource_type": row.resource_type,
        "resource_id": row.resource_id,
        "outcome": row.outcome,
        "ip": row.ip,
        "request_id": row.request_id,
        "details": row.details,
        "created_at": row.created_at.astimezone(UTC).isoformat(),
    }


def compute_hash(prev_hash: str | None, payload: dict[str, Any]) -> str:
    h = hashlib.sha256()
    h.update((prev_hash or "GENESIS").encode())
    h.update(_canonical(payload))
    return h.hexdigest()


def record(
    action: str,
    *,
    org_id: uuid.UUID | None = None,
    actor_user_id: uuid.UUID | None = None,
    actor_type: str = "user",
    actor_label: str | None = None,
    resource_type: str | None = None,
    resource_id: str | uuid.UUID | None = None,
    outcome: str = "success",
    ip: str | None = None,
    details: dict[str, Any] | None = None,
    session: Session | None = None,
) -> None:
    """Append an audit entry. Uses its own transaction unless a control-plane session is given."""

    def _write(db: Session) -> None:
        db.execute(text("SELECT pg_advisory_xact_lock(:k)"), {"k": _CHAIN_LOCK})
        prev = db.execute(select(AuditLog.hash).order_by(AuditLog.id.desc()).limit(1)).scalar()
        row = AuditLog(
            org_id=org_id,
            actor_user_id=actor_user_id,
            actor_type=actor_type,
            actor_label=actor_label,
            action=action,
            resource_type=resource_type,
            resource_id=str(resource_id) if resource_id else None,
            outcome=outcome,
            ip=ip,
            request_id=request_id_var.get(),
            details=details or {},
            created_at=datetime.now(UTC),
            prev_hash=prev,
        )
        row.hash = compute_hash(prev, _entry_payload(row))
        db.add(row)
        db.flush()

    if session is not None:
        _write(session)
    else:
        with control_session() as db:
            _write(db)


def verify_chain(db: Session, limit: int | None = None) -> dict[str, Any]:
    q = select(AuditLog).order_by(AuditLog.id.asc())
    if limit:
        q = q.limit(limit)
    prev: str | None = None
    checked = 0
    for row in db.execute(q).scalars():
        if row.prev_hash != prev:
            return {"valid": False, "checked": checked, "broken_at_id": row.id, "reason": "prev_hash mismatch"}
        expected = compute_hash(prev, _entry_payload(row))
        if expected != row.hash:
            return {"valid": False, "checked": checked, "broken_at_id": row.id, "reason": "hash mismatch"}
        prev = row.hash
        checked += 1
    return {"valid": True, "checked": checked, "head": prev}
