"""Per-organisation usage metering and monthly quota enforcement."""

from __future__ import annotations

import uuid
from datetime import UTC, datetime

from sqlalchemy import select, update
from sqlalchemy.dialects.postgresql import insert
from sqlalchemy.orm import Session

from bankverse.core.errors import QuotaExceeded
from bankverse.db.models.control import OrgQuota, UsageCounter
from bankverse.db.session import control_session

METRICS = (
    "api_calls",
    "fraud_scoring",
    "copilot_queries",
    "document_pages",
    "agent_runs",
    "forecast_runs",
    "credit_scoring",
)

DEFAULT_QUOTAS = {
    "demo": {"api_calls": 200_000, "fraud_scoring": 50_000, "copilot_queries": 2_000, "document_pages": 1_000,
             "agent_runs": 1_000, "forecast_runs": 2_000, "credit_scoring": 20_000},
    "bank": {"api_calls": 2_000_000, "fraud_scoring": 1_000_000, "copilot_queries": 20_000,
             "document_pages": 20_000, "agent_runs": 10_000, "forecast_runs": 20_000, "credit_scoring": 200_000},
}


def current_period() -> str:
    return datetime.now(UTC).strftime("%Y-%m")


def consume(org_id: uuid.UUID, metric: str, amount: int = 1) -> int:
    """Atomically increments the monthly counter (own transaction, independent of the business
    transaction). If the quota would be exceeded the increment is reverted and QuotaExceeded raised."""
    period = current_period()
    with control_session() as s:
        limit = s.execute(
            select(OrgQuota.monthly_limit).where(OrgQuota.org_id == org_id, OrgQuota.metric == metric)
        ).scalar()
        stmt = (
            insert(UsageCounter)
            .values(org_id=org_id, metric=metric, period=period, count=amount)
            .on_conflict_do_update(
                index_elements=[UsageCounter.org_id, UsageCounter.metric, UsageCounter.period],
                set_={"count": UsageCounter.count + amount},
            )
            .returning(UsageCounter.count)
        )
        new_count = int(s.execute(stmt).scalar_one())
        if limit is not None and new_count > limit:
            s.execute(
                update(UsageCounter)
                .where(UsageCounter.org_id == org_id, UsageCounter.metric == metric, UsageCounter.period == period)
                .values(count=UsageCounter.count - amount)
            )
            s.commit()
            raise QuotaExceeded(
                f"Monthly quota exceeded for {metric}",
                details={"metric": metric, "limit": limit, "period": period, "retry_after": 3600},
            )
        return new_count


def usage_for(db: Session, org_id: uuid.UUID, period: str | None = None) -> list[dict[str, object]]:
    period = period or current_period()
    counts = {
        r.metric: r.count
        for r in db.execute(
            select(UsageCounter).where(UsageCounter.org_id == org_id, UsageCounter.period == period)
        ).scalars()
    }
    limits = {
        r.metric: r.monthly_limit
        for r in db.execute(select(OrgQuota).where(OrgQuota.org_id == org_id)).scalars()
    }
    return [
        {"metric": m, "used": counts.get(m, 0), "limit": limits.get(m), "period": period}
        for m in METRICS
    ]
