"""Seed the control plane: platform operator org, the public DEMO bank and a second bank tenant.

Credentials are generated randomly at seed time (never hard-coded) and written to a local,
git-ignored file ``data/.demo_credentials.json``. Users holding roles that require MFA get a TOTP
secret, also stored in that file so that local demos and E2E tests can compute codes.
"""

from __future__ import annotations

import json
import secrets
from pathlib import Path
from typing import Any

from sqlalchemy import select

from bankverse.core.config import get_settings
from bankverse.core.security import encrypt_field, hash_password, new_totp_secret
from bankverse.db.models.control import Membership, Organization, User
from bankverse.db.session import control_session
from bankverse.services import audit, provisioning

DEMO_ORG = "demo-bank"
SECOND_ORG = "banque-alpha"
PLATFORM_ORG = "bankverse-platform"

DEMO_USERS: list[tuple[str, str, str]] = [
    ("admin@demo-bank.example", "Camille Bernard (DEMO)", "bank_org_admin"),
    ("risk@demo-bank.example", "Hugo Lefèvre (DEMO)", "risk_manager"),
    ("fraud@demo-bank.example", "Inès Moreau (DEMO)", "fraud_analyst"),
    ("aml@demo-bank.example", "Yanis Girard (DEMO)", "aml_analyst"),
    ("credit@demo-bank.example", "Léa Fontaine (DEMO)", "credit_analyst"),
    ("ds@demo-bank.example", "Nathan Roux (DEMO)", "data_scientist"),
    ("compliance@demo-bank.example", "Sarah Mercier (DEMO)", "compliance_officer"),
    ("auditor@demo-bank.example", "Paul Garnier (DEMO)", "auditor"),
]
ALPHA_USERS = [
    ("admin@banque-alpha.example", "Admin Banque Alpha (TEST)", "bank_org_admin"),
    ("fraud@banque-alpha.example", "Analyste Alpha (TEST)", "fraud_analyst"),
]
PLATFORM_USERS = [("ops@bankverse.example", "Platform Operator", "platform_super_admin")]


def credentials_path() -> Path:
    return get_settings().data_dir / ".demo_credentials.json"


def _ensure_org(db: Any, slug: str, name: str, kind: str) -> Organization:
    org = db.execute(select(Organization).where(Organization.slug == slug)).scalar_one_or_none()
    if org is None:
        org = provisioning.create_organization(db, slug=slug, name=name, kind=kind)
    return org


def _ensure_user(db: Any, creds: dict[str, Any], org: Organization, email: str, name: str, role: str) -> None:
    user = db.execute(select(User).where(User.email == email)).scalar_one_or_none()
    entry = creds.get(email, {})
    if user is None:
        password = entry.get("password") or secrets.token_urlsafe(12) + "Aa1!"
        user = User(email=email, full_name=name, password_hash=hash_password(password),
                    is_platform_admin=role == "platform_super_admin", locale="fr")
        db.add(user)
        db.flush()
        entry["password"] = password
    if role in get_settings().mfa_required_roles and not user.mfa_enabled:
        secret = entry.get("totp_secret") or new_totp_secret()
        user.mfa_secret_enc = encrypt_field(secret, aad=str(user.id))
        user.mfa_enabled = True
        entry["totp_secret"] = secret
    if not db.execute(select(Membership).where(Membership.user_id == user.id, Membership.org_id == org.id)).first():
        db.add(Membership(user_id=user.id, org_id=org.id, role=role))
    entry.update({"role": role, "org": org.slug, "name": name})
    creds[email] = entry


def seed() -> dict[str, Any]:
    path = credentials_path()
    path.parent.mkdir(parents=True, exist_ok=True)
    creds: dict[str, Any] = json.loads(path.read_text()) if path.exists() else {}
    with control_session(owner=True) as db:
        platform = _ensure_org(db, PLATFORM_ORG, "BANKVERSE AI — Platform Operations", "platform")
        demo = _ensure_org(db, DEMO_ORG, "Banque Démo BANKVERSE (DEMO)", "demo")
        alpha = _ensure_org(db, SECOND_ORG, "Banque Alpha (tenant de test)", "bank")
        for email, name, role in PLATFORM_USERS:
            _ensure_user(db, creds, platform, email, name, role)
        # The operator is also a platform admin inside the DEMO org for support purposes.
        for email, name, role in DEMO_USERS:
            _ensure_user(db, creds, demo, email, name, role)
        for email, name, role in ALPHA_USERS:
            _ensure_user(db, creds, alpha, email, name, role)
        audit.record("platform.seed", actor_type="system", actor_label="seed", session=db,
                     details={"orgs": [PLATFORM_ORG, DEMO_ORG, SECOND_ORG]})
        out = {"demo_org_id": str(demo.id), "alpha_org_id": str(alpha.id), "platform_org_id": str(platform.id)}
    path.write_text(json.dumps(creds, indent=2))
    path.chmod(0o600)
    return out
