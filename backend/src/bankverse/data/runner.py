"""Pipeline execution tracking (runs, steps, rows, errors, retries) persisted in ``platform``."""

from __future__ import annotations

import time
import traceback
import uuid
from collections.abc import Callable, Iterator
from contextlib import contextmanager
from datetime import UTC, datetime
from typing import Any

from bankverse.core.logging import get_logger
from bankverse.core.metrics import PIPELINE_RUNS
from bankverse.db.models.platform import PipelineRun, PipelineStep
from bankverse.db.session import control_session

log = get_logger(__name__)


class RunContext:
    def __init__(self, run_id: uuid.UUID, pipeline: str) -> None:
        self.run_id = run_id
        self.pipeline = pipeline
        self.summary: dict[str, Any] = {}

    @contextmanager
    def step(self, name: str, rows_in: int | None = None) -> Iterator[dict[str, Any]]:
        info: dict[str, Any] = {"rows_out": None, "details": {}}
        started = datetime.now(UTC)
        t0 = time.perf_counter()
        log.info("pipeline_step_start", pipeline=self.pipeline, step=name)
        try:
            yield info
        except Exception as exc:
            self._save_step(name, "failed", rows_in, info.get("rows_out"), started, str(exc)[:2000], info["details"])
            raise
        info["details"]["duration_s"] = round(time.perf_counter() - t0, 3)
        self._save_step(name, "succeeded", rows_in if rows_in is not None else info.get("rows_in"),
                        info.get("rows_out"), started, None, info["details"])
        log.info("pipeline_step_done", pipeline=self.pipeline, step=name, rows_out=info.get("rows_out"),
                 duration_s=info["details"]["duration_s"])

    def _save_step(self, name: str, status: str, rows_in: int | None, rows_out: int | None, started: datetime,
                   error: str | None, details: dict[str, Any]) -> None:
        with control_session(owner=True) as db:
            db.add(PipelineStep(run_id=self.run_id, name=name, status=status, rows_in=rows_in, rows_out=rows_out,
                                started_at=started, finished_at=datetime.now(UTC), error=error,
                                details=_jsonable(details)))


def _jsonable(d: dict[str, Any]) -> dict[str, Any]:
    import orjson

    return orjson.loads(orjson.dumps(d, default=str))


def run_tracked(pipeline: str, fn: Callable[[RunContext], dict[str, Any]], *, params: dict[str, Any] | None = None,
                engine: str = "polars", triggered_by: str = "cli", retries: int = 1,
                org_id: uuid.UUID | None = None) -> dict[str, Any]:
    """Runs ``fn`` with up to ``retries`` additional attempts on transient errors (OSError/timeouts)."""
    attempt = 0
    while True:
        attempt += 1
        with control_session(owner=True) as db:
            run = PipelineRun(pipeline=pipeline, status="running", engine=engine, params=params or {},
                              started_at=datetime.now(UTC), attempts=attempt, triggered_by=triggered_by,
                              org_id=org_id)
            db.add(run)
            db.flush()
            run_id = run.id
        ctx = RunContext(run_id, pipeline)
        try:
            summary = fn(ctx) or {}
            summary = _jsonable({**ctx.summary, **summary})
            with control_session(owner=True) as db:
                r = db.get(PipelineRun, run_id)
                assert r is not None
                r.status = summary.pop("_status", "succeeded")
                r.finished_at = datetime.now(UTC)
                r.summary = summary
            PIPELINE_RUNS.labels(pipeline, "succeeded").inc()
            return {"run_id": str(run_id), "pipeline": pipeline, "status": "succeeded", **summary}
        except Exception as exc:
            transient = isinstance(exc, OSError | TimeoutError)
            with control_session(owner=True) as db:
                r = db.get(PipelineRun, run_id)
                assert r is not None
                r.status = "failed"
                r.finished_at = datetime.now(UTC)
                r.error = f"{type(exc).__name__}: {exc}\n{traceback.format_exc()[-3000:]}"
            PIPELINE_RUNS.labels(pipeline, "failed").inc()
            log.error("pipeline_failed", pipeline=pipeline, attempt=attempt, error=str(exc)[:500])
            if transient and attempt <= retries:
                time.sleep(min(30, 2 ** attempt))
                continue
            raise
