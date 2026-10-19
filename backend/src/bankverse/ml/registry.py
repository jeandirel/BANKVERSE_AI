"""Model registry (PostgreSQL ``platform`` schema) with integrity-checked artifacts.

Lifecycle: ``candidate`` → (human approval, 4-eyes) ``approved`` → (deployment) ``production`` →
``archived``. Rollback re-deploys the previous production version. Inference code only ever loads
the *deployed* version and verifies the artifact SHA-256 before deserialising it.
"""

from __future__ import annotations

import hashlib
import subprocess
import threading
import uuid
from pathlib import Path
from typing import Any

import joblib
from sqlalchemy import func, select, update
from sqlalchemy.orm import Session

from bankverse.core.config import REPO_ROOT, get_settings
from bankverse.core.errors import Conflict, Forbidden, NotFound, Unavailable
from bankverse.db.models.platform import MlModel, ModelApproval, ModelDeployment, ModelVersion
from bankverse.db.session import control_session

_cache: dict[uuid.UUID, Any] = {}
_cache_lock = threading.Lock()


def file_sha256(path: Path) -> str:
    h = hashlib.sha256()
    with path.open("rb") as f:
        for chunk in iter(lambda: f.read(1 << 20), b""):
            h.update(chunk)
    return h.hexdigest()


def git_commit() -> str | None:
    try:
        out = subprocess.run(["git", "-C", str(REPO_ROOT), "rev-parse", "--short", "HEAD"],  # noqa: S603,S607
                             capture_output=True, text=True, timeout=5, check=False)
        return out.stdout.strip() or None
    except Exception:
        return None


def artifact_dir(name: str, version: int) -> Path:
    d = get_settings().models_dir / name / f"v{version}"
    d.mkdir(parents=True, exist_ok=True)
    return d


def next_version(db: Session, name: str) -> int:
    model = db.execute(select(MlModel).where(MlModel.name == name)).scalar_one_or_none()
    if model is None:
        return 1
    current = db.execute(select(func.max(ModelVersion.version)).where(ModelVersion.model_id == model.id)).scalar()
    return int(current or 0) + 1


def register_version(
    db: Session,
    *,
    name: str,
    module: str,
    task: str,
    description: str,
    version: int,
    algorithm: str,
    artifact_path: Path,
    metrics: dict[str, Any],
    params: dict[str, Any],
    features: list[str],
    dataset_versions: list[str],
    reference_profile: dict[str, Any],
    model_card: dict[str, Any],
    mlflow_run_id: str | None,
    trained_by: str,
    training_seconds: float | None,
    champion_candidate: bool = False,
    risk_tier: str = "high",
) -> ModelVersion:
    model = db.execute(select(MlModel).where(MlModel.name == name)).scalar_one_or_none()
    if model is None:
        model = MlModel(name=name, module=module, task=task, description=description, risk_tier=risk_tier)
        db.add(model)
        db.flush()
    else:
        model.description = description
    mv = ModelVersion(
        model_id=model.id,
        version=version,
        algorithm=algorithm,
        status="candidate",
        metrics=metrics,
        params=params,
        features=features,
        dataset_versions=dataset_versions,
        reference_profile=reference_profile,
        artifact_path=str(artifact_path.relative_to(get_settings().artifacts_dir)),
        artifact_sha256=file_sha256(artifact_path),
        mlflow_run_id=mlflow_run_id,
        git_commit=git_commit(),
        trained_by=trained_by,
        training_seconds=training_seconds,
        model_card=model_card,
        is_champion_candidate=champion_candidate,
    )
    db.add(mv)
    db.flush()
    return mv


# ------------------------------------------------------------------------------------------------
# Governance workflow
# ------------------------------------------------------------------------------------------------
def approve(db: Session, mv: ModelVersion, *, reviewer_id: uuid.UUID, reviewer_label: str, decision: str,
            comment: str) -> ModelApproval:
    if mv.status not in ("candidate",):
        raise Conflict(f"Only candidate versions can be reviewed (status={mv.status})")
    if decision not in ("approved", "rejected"):
        raise Conflict("decision must be approved or rejected")
    if mv.trained_by == reviewer_label:
        raise Forbidden("Four-eyes principle: the trainer cannot approve their own model", code="four_eyes")
    if len(comment.strip()) < 10:
        raise Conflict("A justification of at least 10 characters is required")
    appr = ModelApproval(model_version_id=mv.id, decision=decision, reviewer_id=reviewer_id,
                         reviewer_label=reviewer_label, comment=comment)
    mv.status = decision
    db.add(appr)
    db.flush()
    return appr


def current_deployment(db: Session, model_id: uuid.UUID) -> ModelDeployment | None:
    return db.execute(
        select(ModelDeployment).where(ModelDeployment.model_id == model_id, ModelDeployment.active.is_(True))
        .order_by(ModelDeployment.created_at.desc()).limit(1)
    ).scalar_one_or_none()


def deploy(db: Session, mv: ModelVersion, *, actor_label: str, reason: str, action: str = "deploy") -> ModelDeployment:
    if action == "deploy" and mv.status != "approved":
        raise Conflict("Only approved versions can be deployed")
    prev = current_deployment(db, mv.model_id)
    prev_version_id = prev.model_version_id if prev else None
    if prev is not None:
        prev.active = False
        db.execute(update(ModelVersion).where(ModelVersion.id == prev.model_version_id,
                                              ModelVersion.id != mv.id).values(status="archived"))
    mv.status = "production"
    dep = ModelDeployment(model_id=mv.model_id, model_version_id=mv.id, action=action,
                          previous_version_id=prev_version_id, actor_label=actor_label, reason=reason, active=True)
    db.add(dep)
    db.flush()
    with _cache_lock:
        _cache.clear()
    return dep


def rollback(db: Session, model: MlModel, *, actor_label: str, reason: str) -> ModelDeployment:
    cur = current_deployment(db, model.id)
    if cur is None or cur.previous_version_id is None:
        raise Conflict("No previous production version to roll back to")
    target = db.get(ModelVersion, cur.previous_version_id)
    assert target is not None
    cur_mv = db.get(ModelVersion, cur.model_version_id)
    if cur_mv is not None:
        cur_mv.status = "archived"
    return deploy(db, target, actor_label=actor_label, reason=reason, action="rollback")


# ------------------------------------------------------------------------------------------------
# Inference-side loading
# ------------------------------------------------------------------------------------------------
class LoadedModel:
    def __init__(self, name: str, version: ModelVersion, payload: Any) -> None:
        self.name = name
        self.version_id = version.id
        self.version = version.version
        self.tag = f"{name}:v{version.version}"
        self.algorithm = version.algorithm
        self.features = list(version.features)
        self.metrics = dict(version.metrics)
        self.reference_profile = dict(version.reference_profile)
        self.params = dict(version.params)
        self.payload = payload


def load_production(name: str) -> LoadedModel:
    with control_session() as db:
        model = db.execute(select(MlModel).where(MlModel.name == name)).scalar_one_or_none()
        if model is None:
            raise Unavailable(f"Model '{name}' has not been trained in this environment", code="model_unavailable")
        dep = current_deployment(db, model.id)
        if dep is None:
            raise Unavailable(f"Model '{name}' has no approved production deployment", code="model_not_deployed")
        mv = db.get(ModelVersion, dep.model_version_id)
        assert mv is not None
        with _cache_lock:
            cached = _cache.get(mv.id)
        if cached is not None:
            return cached
        path = get_settings().artifacts_dir / mv.artifact_path
        if not path.exists():
            raise Unavailable(f"Artifact of {name}:v{mv.version} is missing", code="artifact_missing")
        if file_sha256(path) != mv.artifact_sha256:
            raise Unavailable(f"Artifact integrity check failed for {name}:v{mv.version}", code="artifact_tampered")
        loaded = LoadedModel(name, mv, joblib.load(path))  # noqa: S301 - integrity verified above
        with _cache_lock:
            _cache[mv.id] = loaded
        return loaded


def get_version(db: Session, name: str, version: int) -> ModelVersion:
    mv = db.execute(
        select(ModelVersion).join(MlModel, MlModel.id == ModelVersion.model_id)
        .where(MlModel.name == name, ModelVersion.version == version)
    ).scalar_one_or_none()
    if mv is None:
        raise NotFound(f"{name}:v{version} not found")
    return mv


def production_models_status() -> list[dict[str, Any]]:
    try:
        with control_session() as db:
            rows = db.execute(
                select(MlModel.name, ModelVersion.version, ModelVersion.algorithm)
                .join(ModelDeployment, ModelDeployment.model_id == MlModel.id)
                .join(ModelVersion, ModelVersion.id == ModelDeployment.model_version_id)
                .where(ModelDeployment.active.is_(True))
            ).all()
            return [{"model": r[0], "version": r[1], "algorithm": r[2]} for r in rows]
    except Exception as exc:  # pragma: no cover
        return [{"error": type(exc).__name__}]


def clear_cache() -> None:
    with _cache_lock:
        _cache.clear()
