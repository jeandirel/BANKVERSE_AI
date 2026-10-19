"""Investigation case helpers shared by fraud, AML, credit and KYC workflows."""

from __future__ import annotations

from datetime import UTC, datetime

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from bankverse.db.models.tenant import Case

PREFIX = {"FR": "FRD", "AM": "AML", "CR": "CRD", "KY": "KYC"}


def new_case_reference(db: Session, kind: str) -> str:
    """Human-readable, per-organisation sequential reference, e.g. AML-2026-00042."""
    year = datetime.now(UTC).year
    prefix = f"{PREFIX.get(kind, kind)}-{year}-"
    count = db.execute(select(func.count()).select_from(Case).where(Case.reference.like(f"{prefix}%"))).scalar_one()
    return f"{prefix}{count + 1:05d}"
