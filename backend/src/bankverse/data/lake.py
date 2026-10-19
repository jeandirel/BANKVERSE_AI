"""Data lake layout (local filesystem or S3-compatible prefix) and dataset version catalogue.

Layout::

    data/raw/<source>/<file>                     immutable downloads (+ _manifest.json)
    data/lake/bronze/<dataset>/part-*.parquet    typed copy of raw, no business rules
    data/lake/silver/<dataset>/...               cleaned, standardised, deduplicated, quality-checked
    data/lake/gold/<dataset>/...                 business aggregates / model features

Every written dataset gets a content fingerprint (SHA-256 of its files), is registered in
``platform.dataset_versions`` and linked to its upstream datasets in ``platform.lineage_edges``.
Re-running a pipeline on identical inputs produces identical fingerprints (idempotence).
"""

from __future__ import annotations

import hashlib
import shutil
import uuid
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

import polars as pl
from sqlalchemy import select, update
from sqlalchemy.dialects.postgresql import insert

from bankverse.core.config import get_settings
from bankverse.db.models.platform import DatasetVersion, LineageEdge
from bankverse.db.session import control_session

LAYERS = ("raw", "bronze", "silver", "gold")


def layer_dir(layer: str, name: str) -> Path:
    s = get_settings()
    base = s.raw_dir if layer == "raw" else s.lake_dir / layer
    return base / name


def fingerprint_path(path: Path) -> str:
    h = hashlib.sha256()
    files = sorted(p for p in path.rglob("*") if p.is_file() and not p.name.startswith("_")) if path.is_dir() else [path]
    for f in files:
        h.update(f.name.encode())
        with f.open("rb") as fh:
            for chunk in iter(lambda: fh.read(1 << 20), b""):
                h.update(chunk)
    return h.hexdigest()


def schema_of(df: pl.DataFrame | pl.LazyFrame) -> list[dict[str, str]]:
    sch = df.collect_schema() if isinstance(df, pl.LazyFrame) else df.schema
    return [{"name": k, "dtype": str(v)} for k, v in sch.items()]


def profile(df: pl.DataFrame, max_cols: int = 60) -> dict[str, Any]:
    """Lightweight profile: null rate, distinct count, min/max/mean for numerics."""
    out: dict[str, Any] = {"rows": df.height, "columns": {}}
    for col in df.columns[:max_cols]:
        s = df.get_column(col)
        info: dict[str, Any] = {"dtype": str(s.dtype), "null_rate": round(s.null_count() / max(df.height, 1), 6)}
        try:
            info["distinct"] = int(s.n_unique())
        except Exception:
            info["distinct"] = None
        if s.dtype.is_numeric() and df.height:
            info.update({"min": _num(s.min()), "max": _num(s.max()), "mean": _num(s.mean()), "std": _num(s.std())})
        out["columns"][col] = info
    return out


def _num(v: Any) -> float | None:
    try:
        return None if v is None else round(float(v), 6)
    except (TypeError, ValueError):
        return None


def write_parquet(df: pl.DataFrame, layer: str, name: str, *, partition_by: str | None = None) -> Path:
    """Atomically (re)writes a dataset directory."""
    target = layer_dir(layer, name)
    tmp = target.with_name(target.name + f".tmp-{uuid.uuid4().hex[:6]}")
    tmp.mkdir(parents=True, exist_ok=True)
    if partition_by:
        for key, part in df.partition_by(partition_by, as_dict=True, maintain_order=True).items():
            val = key[0] if isinstance(key, tuple) else key
            pdir = tmp / f"{partition_by}={val}"
            pdir.mkdir(parents=True, exist_ok=True)
            part.drop(partition_by).write_parquet(pdir / "part-0.parquet", compression="zstd", statistics=True)
    else:
        df.write_parquet(tmp / "part-0.parquet", compression="zstd", statistics=True)
    if target.exists():
        shutil.rmtree(target)
    tmp.rename(target)
    return target


def read_dataset(layer: str, name: str, *, lazy: bool = True) -> pl.LazyFrame | pl.DataFrame:
    path = layer_dir(layer, name)
    if not path.exists():
        raise FileNotFoundError(f"Dataset {layer}/{name} not found — run its pipeline first")
    lf = pl.scan_parquet(str(path / "**" / "*.parquet"), hive_partitioning=True)
    return lf if lazy else lf.collect()


def register_version(
    *,
    name: str,
    layer: str,
    path: Path,
    source_key: str | None,
    rows: int | None,
    columns: int | None,
    schema: list[dict[str, str]],
    prof: dict[str, Any] | None = None,
    run_id: uuid.UUID | None = None,
    upstream_ids: list[uuid.UUID] | None = None,
    transform: str = "",
    collected_at: datetime | None = None,
) -> uuid.UUID:
    fp = fingerprint_path(path)
    size = sum(p.stat().st_size for p in path.rglob("*") if p.is_file()) if path.is_dir() else path.stat().st_size
    with control_session(owner=True) as db:
        existing = db.execute(
            select(DatasetVersion).where(DatasetVersion.name == name, DatasetVersion.layer == layer,
                                         DatasetVersion.fingerprint == fp)
        ).scalar_one_or_none()
        db.execute(update(DatasetVersion).where(DatasetVersion.name == name, DatasetVersion.layer == layer)
                   .values(is_current=False))
        if existing is not None:
            existing.is_current = True
            existing.run_id = run_id or existing.run_id
            vid = existing.id
        else:
            dv = DatasetVersion(
                source_key=source_key, name=name, layer=layer, path=str(path), fingerprint=fp, rows=rows,
                columns=columns, bytes=size, schema_json=schema, profile=prof or {}, run_id=run_id,
                collected_at=collected_at or datetime.now(UTC), is_current=True,
            )
            db.add(dv)
            db.flush()
            vid = dv.id
        for up in upstream_ids or []:
            db.execute(insert(LineageEdge).values(id=uuid.uuid4(), upstream_id=up, downstream_id=vid,
                                                  transform=transform or "transform", run_id=run_id)
                       .on_conflict_do_nothing(constraint="uq_lineage_edge"))
    return vid


def current_version_id(name: str, layer: str) -> uuid.UUID | None:
    with control_session() as db:
        return db.execute(
            select(DatasetVersion.id).where(DatasetVersion.name == name, DatasetVersion.layer == layer,
                                            DatasetVersion.is_current.is_(True))
        ).scalar_one_or_none()


def set_quality_status(version_id: uuid.UUID, status: str) -> None:
    with control_session(owner=True) as db:
        db.execute(update(DatasetVersion).where(DatasetVersion.id == version_id).values(quality_status=status))
