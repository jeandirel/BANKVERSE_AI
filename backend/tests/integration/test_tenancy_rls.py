"""Multi-tenant isolation at the database layer (PostgreSQL row-level security) and in the
control-plane admin API. API-level isolation of business resources is covered per module."""

from __future__ import annotations

import uuid

import pytest
from sqlalchemy import func, select, text

from tests.conftest import login

pytestmark = pytest.mark.integration


def _customer(org_id: uuid.UUID, ref: str):  # noqa: ANN202
    from bankverse.db.models.tenant import Customer

    return Customer(org_id=org_id, external_ref=ref, full_name=f"Client {ref}", is_demo=True)


def test_every_bank_table_has_forced_rls(migrated_db):
    from bankverse.db.session import owner_engine

    with owner_engine().connect() as conn:
        rows = conn.execute(text("""
            SELECT c.relname, c.relrowsecurity, c.relforcerowsecurity,
                   EXISTS (SELECT 1 FROM pg_policies p WHERE p.schemaname='bank' AND p.tablename=c.relname
                           AND p.policyname='tenant_isolation') AS has_policy
            FROM pg_class c JOIN pg_namespace n ON n.oid=c.relnamespace
            JOIN pg_attribute a ON a.attrelid=c.oid AND a.attname='org_id'
            WHERE n.nspname='bank' AND c.relkind='r'""")).all()
    assert len(rows) >= 25
    missing = [r[0] for r in rows if not (r[1] and r[2] and r[3])]
    assert missing == []


def test_rls_hides_other_tenant_rows_even_without_where_clause(tenants):
    from bankverse.db.models.tenant import Customer
    from bankverse.db.session import TenantTarget, tenant_session

    a, b = tenants.org(), tenants.org()
    with tenant_session(TenantTarget(a.id)) as s:
        s.add(_customer(a.id, "A-1"))
    with tenant_session(TenantTarget(b.id)) as s:
        s.add(_customer(b.id, "B-1"))
    with tenant_session(TenantTarget(a.id)) as s:
        refs = s.execute(select(Customer.external_ref)).scalars().all()  # deliberately no org filter
    assert "A-1" in refs and "B-1" not in refs


def test_rls_rejects_cross_tenant_writes(tenants):
    from sqlalchemy.exc import DBAPIError

    from bankverse.db.session import TenantTarget, tenant_session

    a, b = tenants.org(), tenants.org()
    with pytest.raises(DBAPIError):
        with tenant_session(TenantTarget(a.id)) as s:
            s.add(_customer(b.id, "SNEAKY"))  # session bound to A tries to write a row for B
            s.flush()


def test_rls_blocks_update_and_delete_of_other_tenant(tenants):
    from bankverse.db.models.tenant import Customer
    from bankverse.db.session import TenantTarget, tenant_session

    a, b = tenants.org(), tenants.org()
    with tenant_session(TenantTarget(b.id)) as s:
        s.add(_customer(b.id, "B-KEEP"))
    with tenant_session(TenantTarget(a.id)) as s:
        upd = s.execute(text("UPDATE bank.customers SET full_name='pwned' WHERE external_ref='B-KEEP'"))
        dele = s.execute(text("DELETE FROM bank.customers WHERE external_ref='B-KEEP'"))
        assert upd.rowcount == 0 and dele.rowcount == 0
    with tenant_session(TenantTarget(b.id)) as s:
        row = s.execute(select(Customer).where(Customer.external_ref == "B-KEEP")).scalar_one()
        assert row.full_name != "pwned"


def test_rls_fails_closed_without_tenant_context(tenants):
    from bankverse.db.session import app_engine

    a = tenants.org()
    from bankverse.db.session import TenantTarget, tenant_session

    with tenant_session(TenantTarget(a.id)) as s:
        s.add(_customer(a.id, "A-NOCTX"))
    with app_engine().connect() as conn:
        count = conn.execute(text("SELECT count(*) FROM bank.customers")).scalar_one()
    assert count == 0


def test_app_role_cannot_tamper_audit_log(migrated_db):
    from sqlalchemy.exc import DBAPIError

    from bankverse.db.session import app_engine
    from bankverse.services import audit

    audit.record("test.event", actor_type="system", actor_label="pytest")
    with pytest.raises(DBAPIError):
        with app_engine().begin() as conn:
            conn.execute(text("UPDATE control.audit_log SET action='forged'"))
    with pytest.raises(DBAPIError):
        with app_engine().begin() as conn:
            conn.execute(text("DELETE FROM control.audit_log"))


def test_audit_chain_is_valid_and_detects_tampering(migrated_db):
    from bankverse.db.models.control import AuditLog
    from bankverse.db.session import control_session
    from bankverse.services import audit

    for i in range(3):
        audit.record("test.chain", actor_type="system", actor_label="pytest", details={"i": i})
    with control_session() as db:
        result = audit.verify_chain(db)
        assert result["valid"] is True and result["checked"] >= 3
        # Simulate tampering on a detached copy (DB tampering is prevented by grants + trigger).
        last = db.execute(select(AuditLog).order_by(AuditLog.id.desc()).limit(1)).scalar_one()
        payload = audit._entry_payload(last)
        payload["details"] = {"i": 999}
        assert audit.compute_hash(last.prev_hash, payload) != last.hash


def test_admin_api_cannot_touch_members_of_another_org(client, tenants):
    a, b = tenants.org(), tenants.org()
    admin_a = tenants.user(a, "bank_org_admin", mfa=True)
    victim_b = tenants.user(b, "fraud_analyst")
    from bankverse.db.models.control import Membership
    from bankverse.db.session import control_session

    with control_session() as db:
        m_b = db.execute(select(Membership.id).where(Membership.user_id == victim_b["user_id"])).scalar_one()
    headers = login(client, admin_a)
    assert client.patch(f"/api/v1/org/members/{m_b}", json={"role": "auditor"}, headers=headers).status_code == 404
    assert client.delete(f"/api/v1/org/members/{m_b}", headers=headers).status_code == 404
    emails = [m["email"] for m in client.get("/api/v1/org/members").json()]
    assert victim_b["email"] not in emails
    with control_session() as db:
        assert db.execute(select(func.count()).select_from(Membership).where(Membership.id == m_b)).scalar_one() == 1


def test_tenant_id_from_client_is_ignored(client, tenants):
    a, b = tenants.org(), tenants.org()
    creds = tenants.user(a, "auditor")
    login(client, creds)
    # Attempts to smuggle another tenant through params / headers have no effect.
    r = client.get(f"/api/v1/org?org_id={b.id}", headers={"x-org-id": str(b.id)})
    assert r.status_code == 200 and r.json()["id"] == str(a.id)


def test_platform_admin_endpoints_require_platform_admin(client, tenants):
    a = tenants.org()
    creds = tenants.user(a, "bank_org_admin", mfa=True)
    login(client, creds)
    assert client.get("/api/v1/platform/organizations").status_code == 403
