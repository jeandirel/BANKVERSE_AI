"""FastAPI application factory."""

from __future__ import annotations

from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from typing import Any

from fastapi import FastAPI, Request
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse, PlainTextResponse
from prometheus_client import CONTENT_TYPE_LATEST, generate_latest
from sqlalchemy import text

from bankverse import __version__
from bankverse.api.middleware import RequestContextMiddleware, SecurityHeadersMiddleware
from bankverse.api.v1 import api_router
from bankverse.core.config import get_settings
from bankverse.core.errors import install_error_handlers
from bankverse.core.logging import configure_logging, get_logger
from bankverse.db.session import app_engine, dispose_engines

log = get_logger(__name__)


@asynccontextmanager
async def lifespan(_: FastAPI) -> AsyncIterator[None]:
    s = get_settings()
    log.info("startup", env=s.env, version=__version__, llm_provider=s.llm_provider)
    yield
    dispose_engines()


def create_app() -> FastAPI:
    s = get_settings()
    configure_logging(s.log_level, s.log_json)
    app = FastAPI(
        title="BANKVERSE AI API",
        version=__version__,
        description=(
            "Intelligent Banking & Financial Intelligence Platform. Experimental platform working on public "
            "and synthetic data — not authorised to process real banking operations."
        ),
        docs_url=f"{s.api_prefix}/docs",
        openapi_url=f"{s.api_prefix}/openapi.json",
        redoc_url=None,
        lifespan=lifespan,
    )
    app.add_middleware(SecurityHeadersMiddleware)
    app.add_middleware(
        CORSMiddleware,
        allow_origins=s.allowed_origins,
        allow_credentials=True,
        allow_methods=["GET", "POST", "PUT", "PATCH", "DELETE"],
        allow_headers=["content-type", "x-csrf-token", "x-request-id", "authorization"],
    )
    app.add_middleware(RequestContextMiddleware)
    install_error_handlers(app)
    app.include_router(api_router, prefix=s.api_prefix)

    @app.get("/healthz", include_in_schema=False)
    def healthz() -> dict[str, str]:
        return {"status": "ok"}

    @app.get("/readyz", include_in_schema=False)
    def readyz() -> JSONResponse:
        checks: dict[str, Any] = {}
        ok = True
        try:
            with app_engine().connect() as conn:
                conn.execute(text("SELECT 1"))
                checks["database"] = "ok"
                rows = conn.execute(
                    text("SELECT 'control' AS s, version_num FROM control.alembic_version "
                         "UNION ALL SELECT 'tenant', version_num FROM bank.alembic_version")
                ).all()
                checks["migrations"] = {r[0]: r[1] for r in rows}
        except Exception as exc:  # pragma: no cover - exercised in failure drills
            ok = False
            checks["database"] = f"error: {type(exc).__name__}"
        from bankverse.ml.registry import production_models_status

        checks["models"] = production_models_status()
        return JSONResponse({"status": "ok" if ok else "degraded", "checks": checks}, status_code=200 if ok else 503)

    @app.get("/metrics", include_in_schema=False)
    def metrics(request: Request) -> PlainTextResponse:
        token = get_settings().metrics_token
        if token and request.headers.get("authorization") != f"Bearer {token}":
            return PlainTextResponse("forbidden", status_code=403)
        return PlainTextResponse(generate_latest().decode(), media_type=CONTENT_TYPE_LATEST)

    return app


app = create_app()
