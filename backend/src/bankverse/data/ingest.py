"""Download external sources into the immutable raw zone, with integrity verification.

Idempotent: a file already present with the expected hash is not downloaded again.
"""

from __future__ import annotations

import hashlib
import json
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

import httpx
from sqlalchemy import update
from tenacity import retry, retry_if_exception_type, stop_after_attempt, wait_exponential

from bankverse.core.config import get_settings
from bankverse.core.logging import get_logger
from bankverse.data import lake
from bankverse.data.registry import SOURCES, SOURCES_BY_KEY, RemoteFile, SourceSpec, sync_registry
from bankverse.data.runner import RunContext, run_tracked
from bankverse.db.models.platform import DataSource
from bankverse.db.session import control_session

log = get_logger(__name__)


class IntegrityError(Exception):
    pass


def git_blob_sha1(path: Path) -> str:
    size = path.stat().st_size
    h = hashlib.sha1(usedforsecurity=False)
    h.update(f"blob {size}\0".encode())
    with path.open("rb") as f:
        for chunk in iter(lambda: f.read(1 << 20), b""):
            h.update(chunk)
    return h.hexdigest()


def sha256_file(path: Path) -> str:
    h = hashlib.sha256()
    with path.open("rb") as f:
        for chunk in iter(lambda: f.read(1 << 20), b""):
            h.update(chunk)
    return h.hexdigest()


def verify(path: Path, rf: RemoteFile) -> dict[str, Any]:
    result: dict[str, Any] = {"sha256": sha256_file(path), "bytes": path.stat().st_size}
    if rf.size is not None and result["bytes"] != rf.size:
        raise IntegrityError(f"{rf.filename}: size {result['bytes']} != expected {rf.size}")
    if rf.sha256:
        if result["sha256"] != rf.sha256:
            raise IntegrityError(f"{rf.filename}: sha256 mismatch")
        result["verification"] = "sha256 (publisher hash)"
    elif rf.git_blob:
        blob = git_blob_sha1(path)
        if blob != rf.git_blob:
            raise IntegrityError(f"{rf.filename}: git blob mismatch ({blob})")
        result["verification"] = "git blob id (pinned repository content)"
    else:
        result["verification"] = "facts (checked in silver step)"
    return result


@retry(retry=retry_if_exception_type((httpx.TransportError, httpx.HTTPStatusError)),
       stop=stop_after_attempt(3), wait=wait_exponential(min=2, max=20), reraise=True)
def _download(url: str, dest: Path) -> None:
    tmp = dest.with_suffix(dest.suffix + ".part")
    timeout = httpx.Timeout(get_settings().http_timeout_seconds, connect=20)
    with httpx.stream("GET", url, follow_redirects=True, timeout=timeout) as r:
        r.raise_for_status()
        with tmp.open("wb") as f:
            for chunk in r.iter_bytes(1 << 20):
                f.write(chunk)
    tmp.replace(dest)


def _set_status(key: str, status: str, note: str | None = None) -> None:
    with control_session(owner=True) as db:
        values: dict[str, Any] = {"access_status": status}
        if note:
            values["access_note"] = note
        db.execute(update(DataSource).where(DataSource.key == key).values(**values))


def ingest_source(spec: SourceSpec, ctx: RunContext) -> dict[str, Any]:
    out_dir = lake.layer_dir("raw", spec.key)
    out_dir.mkdir(parents=True, exist_ok=True)
    files: list[dict[str, Any]] = []
    for rf in spec.files:
        dest = out_dir / rf.filename
        with ctx.step(f"download:{rf.filename}") as st:
            reused = False
            if dest.exists():
                try:
                    info = verify(dest, rf)
                    reused = True
                except IntegrityError:
                    dest.unlink()
            if not reused:
                _download(rf.url, dest)
                info = verify(dest, rf)
            info.update({"file": rf.filename, "url": rf.url, "reused_existing": reused})
            st["details"] = info
            files.append(info)
    manifest = {
        "source": spec.key, "name": spec.name, "owner": spec.owner, "license": spec.license,
        "original_url": spec.original_url, "collected_at": datetime.now(UTC).isoformat(), "files": files,
    }
    (out_dir / "_manifest.json").write_text(json.dumps(manifest, indent=2))
    vid = lake.register_version(
        name=spec.key, layer="raw", path=out_dir, source_key=spec.key, rows=None, columns=None,
        schema=[], prof={"files": files}, run_id=ctx.run_id, transform="download",
    )
    _set_status(spec.key, "available")
    return {"files": len(files), "bytes": sum(f["bytes"] for f in files), "dataset_version_id": str(vid)}


def ingest_sources(keys: list[str] | None = None) -> dict[str, Any]:
    sync_registry()
    specs = [SOURCES_BY_KEY[k] for k in keys] if keys else [s for s in SOURCES if s.files]
    results: dict[str, Any] = {}
    for spec in specs:
        if not spec.files:
            results[spec.key] = {"status": spec.access_status, "note": spec.access_note}
            continue
        try:
            results[spec.key] = run_tracked(f"ingest:{spec.key}", lambda ctx, s=spec: ingest_source(s, ctx),
                                            engine="python", retries=1)
        except Exception as exc:
            status = "blocked_network" if isinstance(exc, httpx.TransportError | httpx.HTTPStatusError) else "error"
            _set_status(spec.key, status, f"Last ingestion failed: {type(exc).__name__}: {str(exc)[:300]}")
            results[spec.key] = {"status": status, "error": str(exc)[:300]}
    return results
