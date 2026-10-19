"""Thin MLflow wrapper: every training run is logged (params, metrics, artifacts, dataset versions)."""

from __future__ import annotations

import json
import os
from collections.abc import Iterator
from contextlib import contextmanager
from pathlib import Path
from typing import Any

os.environ.setdefault("MLFLOW_DISABLE_AGENT_HINT", "1")

import mlflow  # noqa: E402

from bankverse.core.config import get_settings  # noqa: E402


def _flatten(d: dict[str, Any], prefix: str = "") -> dict[str, float]:
    out: dict[str, float] = {}
    for k, v in d.items():
        key = f"{prefix}{k}"
        if isinstance(v, dict):
            out.update(_flatten(v, key + "."))
        elif isinstance(v, int | float) and not isinstance(v, bool):
            out[key.replace("@", "_at_")] = float(v)
    return out


def _experiment(name: str) -> str:
    s = get_settings()
    mlflow.set_tracking_uri(s.mlflow_tracking_uri)
    exp = mlflow.get_experiment_by_name(name)
    if exp is None:
        s.mlflow_artifact_root.mkdir(parents=True, exist_ok=True)
        return mlflow.create_experiment(name, artifact_location=(s.mlflow_artifact_root / name).as_uri())
    return exp.experiment_id


@contextmanager
def run(experiment: str, run_name: str, tags: dict[str, str] | None = None) -> Iterator[str]:
    exp_id = _experiment(experiment)
    with mlflow.start_run(experiment_id=exp_id, run_name=run_name, tags=tags or {}) as r:
        yield r.info.run_id


def log_params(params: dict[str, Any]) -> None:
    mlflow.log_params({k: (json.dumps(v) if isinstance(v, dict | list) else v) for k, v in params.items()})


def log_metrics(metrics: dict[str, Any], prefix: str = "") -> None:
    flat = _flatten(metrics, prefix)
    if flat:
        mlflow.log_metrics(flat)


def log_json(obj: Any, name: str) -> None:
    mlflow.log_text(json.dumps(obj, indent=2, default=str), name)


def log_file(path: Path) -> None:
    mlflow.log_artifact(str(path))
