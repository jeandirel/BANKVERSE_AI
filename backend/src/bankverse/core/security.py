"""Cryptographic primitives: password hashing, JWT, TOTP MFA, field encryption, API keys."""

from __future__ import annotations

import base64
import hashlib
import hmac
import os
import secrets
import uuid
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from typing import Any, Literal

import jwt
import pyotp
import segno
from argon2 import PasswordHasher
from argon2.exceptions import InvalidHashError, VerificationError, VerifyMismatchError
from cryptography.hazmat.primitives.ciphers.aead import AESGCM

from bankverse.core.config import get_settings

# Argon2id with OWASP-recommended parameters (m=19 MiB, t=2, p=1 is the minimum; we use 64 MiB, t=3).
_hasher = PasswordHasher(time_cost=3, memory_cost=64 * 1024, parallelism=2)

TokenPurpose = Literal["access", "mfa", "mfa_setup"]


# ------------------------------------------------------------------------------------------------
# Passwords
# ------------------------------------------------------------------------------------------------
class PasswordPolicyError(ValueError):
    pass


def validate_password_policy(password: str) -> None:
    """Length-based policy (NIST SP 800-63B): >= 12 chars, not trivially weak."""
    if len(password) < 12:
        raise PasswordPolicyError("Password must contain at least 12 characters.")
    if len(password) > 256:
        raise PasswordPolicyError("Password is too long.")
    lowered = password.lower()
    if len(set(lowered)) < 5 or lowered in {"password1234", "123456789012", "azertyuiopqs"}:
        raise PasswordPolicyError("Password is too weak.")


def hash_password(password: str) -> str:
    return _hasher.hash(password)


def verify_password(password_hash: str, password: str) -> bool:
    try:
        return _hasher.verify(password_hash, password)
    except (VerifyMismatchError, VerificationError, InvalidHashError):
        return False


def needs_rehash(password_hash: str) -> bool:
    return _hasher.check_needs_rehash(password_hash)


# Pre-computed hash used to equalise timing when the user does not exist.
_DUMMY_HASH = _hasher.hash("timing-equaliser-not-a-real-password")


def dummy_verify() -> None:
    verify_password(_DUMMY_HASH, "wrong-password-for-timing")


# ------------------------------------------------------------------------------------------------
# JWT
# ------------------------------------------------------------------------------------------------
@dataclass(frozen=True)
class TokenClaims:
    sub: str
    purpose: TokenPurpose
    sid: str | None
    org: str | None
    mfa: bool
    jti: str
    exp: datetime


def create_token(
    *,
    subject: str,
    purpose: TokenPurpose,
    ttl_seconds: int,
    session_id: str | None = None,
    org_id: str | None = None,
    mfa: bool = False,
) -> str:
    s = get_settings()
    now = datetime.now(UTC)
    payload: dict[str, Any] = {
        "sub": subject,
        "pur": purpose,
        "sid": session_id,
        "org": org_id,
        "mfa": mfa,
        "iat": int(now.timestamp()),
        "nbf": int(now.timestamp()),
        "exp": int((now + timedelta(seconds=ttl_seconds)).timestamp()),
        "iss": s.jwt_issuer,
        "aud": s.jwt_audience,
        "jti": uuid.uuid4().hex,
    }
    return jwt.encode(payload, s.jwt_secret, algorithm="HS256")


class InvalidTokenError(Exception):
    pass


def decode_token(token: str, expected_purpose: TokenPurpose) -> TokenClaims:
    s = get_settings()
    try:
        data = jwt.decode(
            token,
            s.jwt_secret,
            algorithms=["HS256"],
            audience=s.jwt_audience,
            issuer=s.jwt_issuer,
            options={"require": ["exp", "iat", "sub", "jti"]},
        )
    except jwt.PyJWTError as exc:  # expired, bad signature, wrong audience...
        raise InvalidTokenError(str(exc)) from exc
    if data.get("pur") != expected_purpose:
        raise InvalidTokenError("Token purpose mismatch")
    return TokenClaims(
        sub=data["sub"],
        purpose=data["pur"],
        sid=data.get("sid"),
        org=data.get("org"),
        mfa=bool(data.get("mfa")),
        jti=data["jti"],
        exp=datetime.fromtimestamp(data["exp"], tz=UTC),
    )


# ------------------------------------------------------------------------------------------------
# Opaque tokens (refresh tokens, invitations, CSRF)
# ------------------------------------------------------------------------------------------------
def new_opaque_token(nbytes: int = 32) -> str:
    return secrets.token_urlsafe(nbytes)


def hash_opaque_token(token: str) -> str:
    """Opaque tokens are high-entropy: a keyed SHA-256 is appropriate (no need for argon2)."""
    pepper = get_settings().api_key_pepper.encode()
    return hmac.new(pepper, token.encode(), hashlib.sha256).hexdigest()


def constant_time_equals(a: str, b: str) -> bool:
    return hmac.compare_digest(a.encode(), b.encode())


# ------------------------------------------------------------------------------------------------
# API keys: "bvk_<prefix>_<secret>" — prefix is stored in clear for lookup, secret only hashed.
# ------------------------------------------------------------------------------------------------
def generate_api_key() -> tuple[str, str, str]:
    prefix = secrets.token_hex(4)
    secret = secrets.token_urlsafe(32)
    full = f"bvk_{prefix}_{secret}"
    return full, prefix, hash_opaque_token(full)


def parse_api_key(raw: str) -> str | None:
    parts = raw.split("_", 2)
    if len(parts) != 3 or parts[0] != "bvk" or len(parts[1]) != 8:
        return None
    return parts[1]


# ------------------------------------------------------------------------------------------------
# TOTP MFA (RFC 6238)
# ------------------------------------------------------------------------------------------------
def new_totp_secret() -> str:
    return pyotp.random_base32()


def totp_provisioning_uri(secret: str, email: str) -> str:
    return pyotp.TOTP(secret).provisioning_uri(name=email, issuer_name="BANKVERSE AI")


def totp_qr_svg(uri: str) -> str:
    qr = segno.make(uri, error="m")
    return qr.svg_inline(scale=4, dark="#0B1220", light="#FFFFFF")


def verify_totp(secret: str, code: str) -> bool:
    code = code.strip().replace(" ", "")
    if not code.isdigit() or len(code) != 6:
        return False
    return pyotp.TOTP(secret).verify(code, valid_window=1)


# ------------------------------------------------------------------------------------------------
# Field-level encryption (AES-256-GCM). In AWS the key is a KMS data key (envelope encryption).
# ------------------------------------------------------------------------------------------------
def encrypt_field(plaintext: str, *, aad: str = "") -> str:
    key = get_settings().encryption_key_bytes
    nonce = os.urandom(12)
    ct = AESGCM(key).encrypt(nonce, plaintext.encode(), aad.encode() or None)
    return "v1:" + base64.urlsafe_b64encode(nonce + ct).decode()


def decrypt_field(token: str, *, aad: str = "") -> str:
    if not token.startswith("v1:"):
        raise ValueError("Unknown ciphertext version")
    raw = base64.urlsafe_b64decode(token[3:])
    nonce, ct = raw[:12], raw[12:]
    key = get_settings().encryption_key_bytes
    return AESGCM(key).decrypt(nonce, ct, aad.encode() or None).decode()


def sha256_hex(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()
