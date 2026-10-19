"""Centralised, typed configuration.

All settings come from environment variables prefixed with ``BANKVERSE_`` (or a ``.env`` file in
local development). No secret has a usable default outside the ``local`` environment: the
application refuses to start in ``staging``/``pilot`` if a development secret is still configured.
"""

from __future__ import annotations

import base64
import secrets
from functools import lru_cache
from pathlib import Path
from typing import Literal

from pydantic import Field, field_validator, model_validator
from pydantic_settings import BaseSettings, SettingsConfigDict

REPO_ROOT = Path(__file__).resolve().parents[4]
_DEV_MARKER = "dev-only-insecure"


class Settings(BaseSettings):
    model_config = SettingsConfigDict(
        env_prefix="BANKVERSE_",
        env_file=(str(REPO_ROOT / ".env"), ".env"),
        env_file_encoding="utf-8",
        extra="ignore",
    )

    env: Literal["local", "test", "staging", "pilot"] = "local"
    app_name: str = "BANKVERSE AI"
    api_prefix: str = "/api/v1"
    log_level: str = "INFO"
    log_json: bool = True

    # --- Databases -------------------------------------------------------------------------
    # Owner role: used by migrations and offline jobs (bypasses nothing: RLS is FORCED on tenant tables).
    database_url: str = "postgresql+psycopg://bankverse_owner:bankverse_owner_dev@127.0.0.1:5432/bankverse"
    # Runtime role used by the API: least privilege, subject to row-level security.
    app_database_url: str = "postgresql+psycopg://bankverse_app:bankverse_app_dev@127.0.0.1:5432/bankverse"
    db_pool_size: int = 10
    db_max_overflow: int = 10
    db_echo: bool = False

    # --- Security --------------------------------------------------------------------------
    jwt_secret: str = f"{_DEV_MARKER}-jwt-secret-change-me-0123456789abcdef"
    jwt_issuer: str = "bankverse-ai"
    jwt_audience: str = "bankverse-api"
    access_token_ttl_seconds: int = 15 * 60
    refresh_token_ttl_seconds: int = 12 * 3600
    mfa_token_ttl_seconds: int = 5 * 60
    # 32-byte key, base64; encrypts MFA secrets & connector credentials at rest (envelope key from KMS in AWS)
    data_encryption_key: str = Field(
        default_factory=lambda: base64.b64encode(b"dev-only-insecure-key-32bytes!!!").decode()
    )
    api_key_pepper: str = f"{_DEV_MARKER}-pepper"
    cookie_secure: bool = False
    cookie_domain: str | None = None
    allowed_origins: list[str] = ["http://localhost:3000", "http://127.0.0.1:3000"]
    max_failed_logins: int = 5
    lockout_minutes: int = 15
    mfa_required_roles: list[str] = ["platform_super_admin", "bank_org_admin", "compliance_officer"]
    enforce_mfa: bool = True
    # One-click sessions on the public DEMO tenant (non-admin roles only). Disable for bank pilots.
    public_demo_enabled: bool = True
    public_demo_org_slug: str = "demo-bank"

    # --- Rate limiting / quotas ------------------------------------------------------------
    redis_url: str | None = None
    rate_limit_auth_per_minute: int = 10
    rate_limit_api_per_minute: int = 600
    metrics_token: str | None = None

    # --- Storage & data lake ---------------------------------------------------------------
    data_dir: Path = REPO_ROOT / "data"
    artifacts_dir: Path = REPO_ROOT / "artifacts"
    storage_backend: Literal["local", "s3"] = "local"
    storage_local_dir: Path = REPO_ROOT / "data" / "object_store"
    s3_bucket_documents: str | None = None
    s3_bucket_lake: str | None = None
    aws_region: str = "eu-west-3"
    kms_key_id: str | None = None
    max_upload_bytes: int = 15 * 1024 * 1024

    # --- MLOps -----------------------------------------------------------------------------
    mlflow_tracking_uri: str = Field(default_factory=lambda: f"sqlite:///{REPO_ROOT / 'artifacts' / 'mlflow.db'}")
    mlflow_artifact_root: Path = REPO_ROOT / "artifacts" / "mlruns"

    # --- LLM providers ---------------------------------------------------------------------
    llm_provider: Literal["none", "bedrock", "anthropic"] = "none"
    bedrock_model_id: str | None = None
    anthropic_model: str | None = None
    anthropic_api_key: str | None = None
    llm_max_tokens: int = 1200
    llm_timeout_seconds: float = 45.0

    # --- External data connectors ----------------------------------------------------------
    fred_api_key: str | None = None
    sec_user_agent: str | None = None  # SEC requires "Company Name contact@domain"
    http_timeout_seconds: float = 60.0

    # --- Agents ----------------------------------------------------------------------------
    agent_max_steps: int = 8
    agent_timeout_seconds: float = 60.0

    @field_validator("allowed_origins", "mfa_required_roles", mode="before")
    @classmethod
    def _split_csv(cls, v: object) -> object:
        if isinstance(v, str) and not v.strip().startswith("["):
            return [s.strip() for s in v.split(",") if s.strip()]
        return v

    @model_validator(mode="after")
    def _refuse_dev_secrets_outside_local(self) -> Settings:
        if self.env in ("staging", "pilot"):
            weak = [
                name
                for name in ("jwt_secret", "api_key_pepper")
                if _DEV_MARKER in getattr(self, name)
            ]
            if base64.b64decode(self.data_encryption_key).startswith(b"dev-only"):
                weak.append("data_encryption_key")
            if weak:
                raise ValueError(f"Insecure development secrets configured for env={self.env}: {weak}")
            if not self.cookie_secure:
                raise ValueError("cookie_secure must be true outside local/test environments")
        return self

    @property
    def encryption_key_bytes(self) -> bytes:
        key = base64.b64decode(self.data_encryption_key)
        if len(key) != 32:
            raise ValueError("BANKVERSE_DATA_ENCRYPTION_KEY must decode to exactly 32 bytes")
        return key

    @property
    def lake_dir(self) -> Path:
        return self.data_dir / "lake"

    @property
    def raw_dir(self) -> Path:
        return self.data_dir / "raw"

    @property
    def models_dir(self) -> Path:
        return self.artifacts_dir / "models"


@lru_cache(maxsize=1)
def get_settings() -> Settings:
    return Settings()


def generate_secret(nbytes: int = 32) -> str:
    """Helper used by ``bankverse secrets generate`` to bootstrap non-local environments."""
    return secrets.token_urlsafe(nbytes)
