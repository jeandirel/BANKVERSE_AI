"""Authentication, MFA, CSRF, lockout, sessions and organisation switching."""

from __future__ import annotations

import pyotp
import pytest

from tests.conftest import TEST_PASSWORD, login

pytestmark = pytest.mark.integration


def test_login_success_sets_httponly_cookies(client, tenants):
    org = tenants.org()
    creds = tenants.user(org, "fraud_analyst")
    r = client.post("/api/v1/auth/login", json={"email": creds["email"], "password": TEST_PASSWORD})
    assert r.status_code == 200
    set_cookie = r.headers.get_list("set-cookie")
    access = next(c for c in set_cookie if c.startswith("bv_access="))
    assert "HttpOnly" in access and "SameSite=lax" in access
    csrf = next(c for c in set_cookie if c.startswith("bv_csrf="))
    assert "HttpOnly" not in csrf
    me = client.get("/api/v1/auth/me").json()
    assert me["org"]["slug"] == org.slug
    assert me["role"] == "fraud_analyst"
    assert "fraud.triage" in me["permissions"]


def test_invalid_credentials_are_generic(client, tenants):
    org = tenants.org()
    creds = tenants.user(org, "auditor")
    bad = client.post("/api/v1/auth/login", json={"email": creds["email"], "password": "wrong-password-123"})
    unknown = client.post("/api/v1/auth/login", json={"email": "nobody@x.io", "password": "wrong-password-123"})
    assert bad.status_code == unknown.status_code == 401
    assert bad.json()["error"]["code"] == unknown.json()["error"]["code"] == "invalid_credentials"


def test_account_lockout_after_repeated_failures(client, tenants):
    org = tenants.org()
    creds = tenants.user(org, "auditor")
    for _ in range(5):
        client.post("/api/v1/auth/login", json={"email": creds["email"], "password": "wrong-password-123"})
    # Correct password is now refused while locked.
    r = client.post("/api/v1/auth/login", json={"email": creds["email"], "password": TEST_PASSWORD})
    assert r.status_code == 401
    from sqlalchemy import select

    from bankverse.db.models.control import SecurityEvent
    from bankverse.db.session import control_session

    with control_session() as db:
        kinds = db.execute(select(SecurityEvent.kind).where(SecurityEvent.user_id == creds["user_id"])).scalars().all()
    assert "brute_force" in kinds


def test_mfa_required_and_verified(client, tenants):
    org = tenants.org()
    creds = tenants.user(org, "compliance_officer", mfa=True)
    r = client.post("/api/v1/auth/login", json={"email": creds["email"], "password": TEST_PASSWORD})
    body = r.json()
    assert body["status"] == "mfa_required" and "bv_access" not in r.cookies
    bad = client.post("/api/v1/auth/mfa/verify", json={"mfa_token": body["mfa_token"], "code": "000000"})
    assert bad.status_code == 401
    good = client.post("/api/v1/auth/mfa/verify",
                       json={"mfa_token": body["mfa_token"], "code": pyotp.TOTP(creds["totp"]).now()})
    assert good.status_code == 200
    assert client.get("/api/v1/auth/me").json()["mfa_verified"] is True


def test_admin_role_without_mfa_must_enrol(client, tenants):
    org = tenants.org()
    creds = tenants.user(org, "bank_org_admin", mfa=False)
    r = client.post("/api/v1/auth/login", json={"email": creds["email"], "password": TEST_PASSWORD}).json()
    assert r["status"] == "mfa_setup_required"
    setup = client.post("/api/v1/auth/mfa/setup/start", json={"setup_token": r["setup_token"]}).json()
    assert setup["qr_svg"].startswith("<svg")
    done = client.post("/api/v1/auth/mfa/setup/confirm",
                       json={"setup_token": r["setup_token"], "code": pyotp.TOTP(setup["secret"]).now()})
    assert done.status_code == 200
    assert client.get("/api/v1/org/members").status_code == 200


def test_csrf_required_for_unsafe_cookie_requests(client, tenants):
    org = tenants.org()
    creds = tenants.user(org, "bank_org_admin", mfa=True)
    headers = login(client, creds)
    no_csrf = client.patch("/api/v1/org", json={"settings": {"fraud_alert_threshold": 0.6}})
    assert no_csrf.status_code == 403 and no_csrf.json()["error"]["code"] == "csrf_failed"
    ok = client.patch("/api/v1/org", json={"settings": {"fraud_alert_threshold": 0.6}}, headers=headers)
    assert ok.status_code == 200, ok.text
    evil_origin = client.patch("/api/v1/org", json={"settings": {"fraud_alert_threshold": 0.7}},
                               headers={**headers, "origin": "https://evil.example"})
    assert evil_origin.status_code == 403


def test_refresh_rotates_and_logout_revokes(client, tenants):
    org = tenants.org()
    creds = tenants.user(org, "auditor")
    login(client, creds)
    old_refresh = client.cookies.get("bv_refresh", path="/api/v1/auth")
    r = client.post("/api/v1/auth/refresh")
    assert r.status_code == 200
    assert client.cookies.get("bv_refresh", path="/api/v1/auth") != old_refresh
    # The previous refresh token is no longer valid after rotation.
    from fastapi.testclient import TestClient

    other = TestClient(client.app, base_url="http://testserver")
    other.cookies.set("bv_refresh", old_refresh, path="/api/v1/auth")
    assert other.post("/api/v1/auth/refresh").status_code == 401
    csrf = r.json()["csrf_token"]
    client.post("/api/v1/auth/logout", headers={"x-csrf-token": csrf})
    assert client.get("/api/v1/auth/me").status_code == 401


def test_switch_org_requires_membership(client, tenants):
    a, b, c = tenants.org(), tenants.org(), tenants.org()
    creds = tenants.user(a, "auditor")
    # same user is also member of B
    from bankverse.db.models.control import Membership
    from bankverse.db.session import control_session

    with control_session(owner=True) as db:
        db.add(Membership(user_id=creds["user_id"], org_id=b.id, role="risk_manager"))
    headers = login(client, creds, org_slug=a.slug)
    ok = client.post("/api/v1/auth/switch-org", json={"org_id": str(b.id)}, headers=headers)
    assert ok.status_code == 200 and ok.json()["org"]["slug"] == b.slug
    assert client.get("/api/v1/auth/me").json()["role"] == "risk_manager"
    denied = client.post("/api/v1/auth/switch-org", json={"org_id": str(c.id)},
                         headers={"x-csrf-token": ok.json()["csrf_token"]})
    assert denied.status_code == 403


def test_suspended_org_blocks_access(client, tenants):
    org = tenants.org()
    creds = tenants.user(org, "auditor")
    login(client, creds)
    from bankverse.db.models.control import Organization
    from bankverse.db.session import control_session
    from bankverse.services.provisioning import suspend

    with control_session(owner=True) as db:
        suspend(db, db.get(Organization, org.id))
    assert client.get("/api/v1/auth/me").status_code in (401, 403)


def test_api_key_authentication_and_revocation(client, tenants):
    org = tenants.org()
    admin = tenants.user(org, "bank_org_admin", mfa=True)
    headers = login(client, admin)
    created = client.post("/api/v1/org/api-keys", json={"name": "core-banking-feed", "role": "fraud_analyst"},
                          headers=headers).json()
    key = created["api_key"]
    from fastapi.testclient import TestClient

    svc = TestClient(client.app, base_url="http://testserver")
    me = svc.get("/api/v1/auth/me", headers={"authorization": f"Bearer {key}"})
    assert me.status_code == 200 and me.json()["kind"] == "api_key" and me.json()["role"] == "fraud_analyst"
    admin_role = client.post("/api/v1/org/api-keys", json={"name": "bad", "role": "bank_org_admin"}, headers=headers)
    assert admin_role.status_code == 403
    client.delete(f"/api/v1/org/api-keys/{created['id']}", headers=headers)
    assert svc.get("/api/v1/auth/me", headers={"authorization": f"Bearer {key}"}).status_code == 401
    forged = key[:-4] + "AAAA"
    assert svc.get("/api/v1/auth/me", headers={"authorization": f"Bearer {forged}"}).status_code == 401
