"""Model governance operations from the CLI (approvals are attributed to a named reviewer)."""

from __future__ import annotations

import argparse
import uuid
from typing import Any

from sqlalchemy import select

from bankverse.db.models.platform import MlModel, ModelVersion
from bankverse.db.session import control_session
from bankverse.ml import registry
from bankverse.services import audit


def models_cli(a: argparse.Namespace) -> Any:
    with control_session(owner=True) as db:
        if a.action == "list":
            rows = db.execute(select(MlModel.name, ModelVersion.version, ModelVersion.status, ModelVersion.algorithm,
                                     ModelVersion.created_at).join(ModelVersion, ModelVersion.model_id == MlModel.id)
                              .order_by(MlModel.name, ModelVersion.version)).all()
            return [dict(r._mapping) for r in rows]
        if not a.name or not a.version:
            raise SystemExit("--name and --version are required")
        mv = registry.get_version(db, a.name, a.version)
        if a.action in ("approve", "reject"):
            if not a.reviewer or len(a.comment) < 10:
                raise SystemExit("--reviewer and a --comment of at least 10 characters are required")
            appr = registry.approve(db, mv, reviewer_id=uuid.uuid5(uuid.NAMESPACE_DNS, a.reviewer),
                                    reviewer_label=a.reviewer,
                                    decision="approved" if a.action == "approve" else "rejected", comment=a.comment)
            audit.record(f"model.{appr.decision}", actor_type="user", actor_label=a.reviewer, resource_type="model_version",
                         resource_id=str(mv.id), details={"model": a.name, "version": a.version, "via": "cli",
                                                          "comment": a.comment}, session=db)
            return {"model": a.name, "version": a.version, "status": mv.status}
        if a.action == "deploy":
            dep = registry.deploy(db, mv, actor_label=a.reviewer or "cli", reason=a.comment or "deploy")
            audit.record("model.deployed", actor_type="user", actor_label=a.reviewer or "cli",
                         resource_type="model_version", resource_id=str(mv.id),
                         details={"model": a.name, "version": a.version, "via": "cli"}, session=db)
            return {"model": a.name, "version": a.version, "status": mv.status, "deployment": str(dep.id)}
        if a.action == "rollback":
            dep = registry.rollback(db, mv.model, actor_label=a.reviewer or "cli", reason=a.comment or "rollback")
            return {"model": a.name, "rolled_back_to": str(dep.model_version_id)}
    return None
