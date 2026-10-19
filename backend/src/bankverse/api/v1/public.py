"""Public, unauthenticated endpoints for the marketing site: measured evidence only (no tenant data)."""

from __future__ import annotations

from typing import Any

from fastapi import APIRouter, Depends, Request
from sqlalchemy import select
from sqlalchemy.orm import Session

from bankverse.auth.deps import client_ip, control_db
from bankverse.core.config import get_settings
from bankverse.core.ratelimit import limiter
from bankverse.db.models.platform import DataSource, DatasetVersion
from bankverse.modules.governance import monitoring

router = APIRouter(prefix="/public", tags=["public"])


@router.get("/evidence")
def evidence(request: Request, db: Session = Depends(control_db)) -> dict[str, Any]:
    limiter.hit(f"public:ip:{client_ip(request)}", 120)
    inv = monitoring.inventory()
    used = {s for (s,) in db.execute(select(DatasetVersion.source_key).distinct()).all() if s}
    sources = [{"key": s.key, "name": s.name, "license": s.license, "kind": s.kind}
               for s in db.execute(select(DataSource).order_by(DataSource.kind, DataSource.key)).scalars() if s.key in used]
    return {"models": [{"name": m["name"], "module": m["module"], "task": m["task"], "version": m["production"]["version"],
                        "algorithm": m["production"]["algorithm"], "headline": m["production"]["headline"], "deployed_at": m["production"]["deployed_at"]}
                       for m in inv if m["production"]],
            "sources": sources, "demo_enabled": get_settings().public_demo_enabled,
            "notice": "Métriques mesurées sur des jeux de test publics ou synthétiques documentés ; aucune certification n'est revendiquée."}
