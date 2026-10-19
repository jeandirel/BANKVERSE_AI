"""Runs the deployed models over a DEMO/test organisation (after generation or a new deployment)."""

from __future__ import annotations

from typing import Any

from sqlalchemy import select

from bankverse.core.errors import Unavailable
from bankverse.db.models.control import Organization
from bankverse.db.session import TenantTarget, control_session, tenant_session
from bankverse.services import audit


def score_demo_bank(org_slug: str = "demo-bank") -> dict[str, Any]:
    with control_session() as db:
        org = db.execute(select(Organization).where(Organization.slug == org_slug)).scalar_one()
        target = TenantTarget(org.id, org.isolation_mode, org.dedicated_db_secret)
        org_id = org.id
    out: dict[str, Any] = {}
    steps = []
    from bankverse.modules.fraud.service import batch_score_org as fraud_batch

    steps.append(("fraud", fraud_batch))
    try:
        from bankverse.modules.aml.service import batch_score_org as aml_batch

        steps.append(("aml", aml_batch))
    except ImportError:
        pass
    try:
        from bankverse.modules.credit.service import batch_score_org as credit_batch

        steps.append(("credit", credit_batch))
    except ImportError:
        pass
    try:
        from bankverse.modules.customer.service import batch_score_org as customer_batch

        steps.append(("customer", customer_batch))
    except ImportError:
        pass
    for name, fn in steps:
        try:
            with tenant_session(target, owner=True) as s:
                out[name] = fn(s, org_id)
        except Unavailable as exc:
            out[name] = {"status": "skipped", "reason": exc.message}
    audit.record("demo.scored", org_id=org_id, actor_type="system", actor_label="demo-scoring",
                 details={k: {kk: vv for kk, vv in v.items() if isinstance(vv, int | float | str)} for k, v in out.items()})
    return out
