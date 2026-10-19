"""Shared fixtures. Integration tests run against a real PostgreSQL database (``bankverse_test``),
migrated with Alembic exactly like production. Nothing is mocked at the database layer."""

from __future__ import annotations

import os
import subprocess
import sys
import uuid
from collections.abc import Iterator
from pathlib import Path

import pyotp
import pytest

BACKEND = Path(__file__).resolve().parents[1]
os.environ.setdefault("BANKVERSE_ENV", "test")
os.environ.setdefault(
    "BANKVERSE_DATABASE_URL",
    "postgresql+psycopg://bankverse_owner:bankverse_owner_dev@127.0.0.1:5432/bankverse_test",
)
os.environ.setdefault(
    "BANKVERSE_APP_DATABASE_URL",
    "postgresql+psycopg://bankverse_app:bankverse_app_dev@127.0.0.1:5432/bankverse_test",
)
os.environ.setdefault("BANKVERSE_RATE_LIMIT_AUTH_PER_MINUTE", "1000")
os.environ.setdefault("BANKVERSE_LOG_LEVEL", "WARNING")

TEST_PASSWORD = "Correct-Horse-Battery-42"


@pytest.fixture(scope="session")
def migrated_db() -> None:
    for stream in ("control", "tenant"):
        subprocess.run(
            [sys.executable, "-m", "alembic", "-c", str(BACKEND / "alembic.ini"), "-n", stream, "upgrade", "head"],
            check=True, cwd=BACKEND, capture_output=True,
        )


@pytest.fixture(scope="session")
def app(migrated_db: None):  # noqa: ANN201
    from bankverse.api.app import create_app

    return create_app()


@pytest.fixture()
def client(app):  # noqa: ANN001, ANN201
    from fastapi.testclient import TestClient

    from bankverse.core.ratelimit import limiter

    limiter.reset()
    with TestClient(app, base_url="http://testserver") as c:
        yield c


class TenantFactory:
    """Creates isolated organisations and users for a test (unique slugs, no shared state)."""

    def __init__(self) -> None:
        self.created: list[uuid.UUID] = []

    def org(self, prefix: str = "bank", **kw: object):  # noqa: ANN201
        from bankverse.db.session import control_session
        from bankverse.services.provisioning import create_organization

        slug = f"{prefix}-{uuid.uuid4().hex[:10]}"
        with control_session(owner=True) as db:
            org = create_organization(db, slug=slug, name=f"Test {slug}", **kw)  # type: ignore[arg-type]
            db.flush()
            self.created.append(org.id)
            return org

    def user(self, org, role: str, *, mfa: bool = False, email: str | None = None) -> dict[str, str]:  # noqa: ANN001
        from bankverse.core.security import encrypt_field, hash_password, new_totp_secret
        from bankverse.db.models.control import Membership, User
        from bankverse.db.session import control_session

        email = email or f"{role}-{uuid.uuid4().hex[:8]}@test.example"
        secret = new_totp_secret() if mfa else ""
        with control_session(owner=True) as db:
            u = User(email=email, full_name=f"Test {role}", password_hash=hash_password(TEST_PASSWORD))
            db.add(u)
            db.flush()
            if mfa:
                u.mfa_secret_enc = encrypt_field(secret, aad=str(u.id))
                u.mfa_enabled = True
            db.add(Membership(user_id=u.id, org_id=org.id, role=role))
            return {"email": email, "password": TEST_PASSWORD, "totp": secret, "user_id": str(u.id)}


@pytest.fixture()
def tenants(migrated_db: None) -> Iterator[TenantFactory]:
    yield TenantFactory()


def login(client, creds: dict[str, str], org_slug: str | None = None) -> dict[str, str]:  # noqa: ANN001
    """Logs in (completing TOTP if needed) and returns headers carrying the CSRF token."""
    r = client.post("/api/v1/auth/login", json={"email": creds["email"], "password": creds["password"],
                                                 "org_slug": org_slug})
    assert r.status_code == 200, r.text
    body = r.json()
    if body["status"] == "mfa_required":
        code = pyotp.TOTP(creds["totp"]).now()
        r = client.post("/api/v1/auth/mfa/verify", json={"mfa_token": body["mfa_token"], "code": code})
        assert r.status_code == 200, r.text
        body = r.json()
    assert body["status"] == "ok", body
    return {"x-csrf-token": body["csrf_token"]}
