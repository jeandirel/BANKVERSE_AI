"""Data platform API: sources registry, dataset catalogue (medallion layers), quality results, lineage, pipeline
runs, and tenant data uploads (CSV/Parquet profiling and schema checks)."""

from __future__ import annotations

import hashlib
import io
import uuid
from typing import Any, Literal

import polars as pl
from fastapi import APIRouter, Depends, File, Form, Query, Request, UploadFile
from sqlalchemy import func, select
from sqlalchemy.orm import Session

from bankverse.auth.deps import Principal, client_ip, control_db, require, tenant_db
from bankverse.core.config import get_settings
from bankverse.core.errors import Conflict, NotFound, ValidationFailed
from bankverse.core.storage import get_store, org_key
from bankverse.data import lake
from bankverse.db.models.platform import (
    DatasetVersion,
    DataSource,
    LineageEdge,
    MlModel,
    ModelVersion,
    PipelineRun,
    PipelineStep,
    QualityResult,
)
from bankverse.db.models.tenant import OrgDataset
from bankverse.services import audit

router = APIRouter(prefix="/data", tags=["data"])

TEMPLATES: dict[str, dict[str, Any]] = {
    "card_transactions": {"required": {"ts": "datetime", "amount": "numeric", "merchant_name": "string", "mcc": "integer", "card_ref": "string"},
                          "label": "Transactions carte"},
    "transfers": {"required": {"ts": "datetime", "amount": "numeric", "src_account": "string", "dst_account": "string"}, "label": "Virements"},
    "customers": {"required": {"customer_ref": "string", "segment": "string"}, "label": "Clients"},
    "generic": {"required": {}, "label": "Jeu de données générique"},
}


@router.get("/sources")
def sources(p: Principal = Depends(require("data.read")), cdb: Session = Depends(control_db)) -> list[dict[str, Any]]:
    counts = dict(cdb.execute(select(DatasetVersion.source_key, func.count()).group_by(DatasetVersion.source_key)).all())
    last = dict(cdb.execute(select(DatasetVersion.source_key, func.max(DatasetVersion.created_at)).group_by(DatasetVersion.source_key)).all())
    return [{"key": s.key, "name": s.name, "owner": s.owner, "kind": s.kind, "license": s.license, "restrictions": s.restrictions,
             "redistribution": s.redistribution, "original_url": s.original_url, "modules": s.modules, "access_status": s.access_status,
             "access_note": s.access_note, "commercial_use_ok": s.commercial_use_ok, "dataset_versions": counts.get(s.key, 0),
             "last_version_at": last.get(s.key)} for s in cdb.execute(select(DataSource).order_by(DataSource.kind, DataSource.key)).scalars()]


@router.get("/datasets")
def datasets(p: Principal = Depends(require("data.read")), cdb: Session = Depends(control_db),
             layer: Literal["raw", "bronze", "silver", "gold", "features"] | None = None) -> list[dict[str, Any]]:
    q = select(DatasetVersion).where(DatasetVersion.is_current.is_(True))
    if layer:
        q = q.where(DatasetVersion.layer == layer)
    order = {"raw": 0, "bronze": 1, "silver": 2, "gold": 3, "features": 4}
    rows = sorted(cdb.execute(q).scalars(), key=lambda d: (d.source_key or "", order.get(d.layer, 9), d.name))
    return [{"id": str(d.id), "name": d.name, "layer": d.layer, "source_key": d.source_key, "rows": d.rows, "columns": d.columns, "bytes": d.bytes,
             "quality_status": d.quality_status, "fingerprint": d.fingerprint[:16], "created_at": d.created_at} for d in rows]


@router.get("/datasets/{version_id}")
def dataset(version_id: uuid.UUID, p: Principal = Depends(require("data.read")), cdb: Session = Depends(control_db)) -> dict[str, Any]:
    d = cdb.get(DatasetVersion, version_id)
    if d is None:
        raise NotFound("Dataset version not found")
    checks = cdb.execute(select(QualityResult).where(QualityResult.dataset_version_id == d.id).order_by(QualityResult.status.desc())).scalars().all()
    up = cdb.execute(select(DatasetVersion, LineageEdge.transform).join(LineageEdge, LineageEdge.upstream_id == DatasetVersion.id)
                     .where(LineageEdge.downstream_id == d.id)).all()
    down = cdb.execute(select(DatasetVersion, LineageEdge.transform).join(LineageEdge, LineageEdge.downstream_id == DatasetVersion.id)
                       .where(LineageEdge.upstream_id == d.id)).all()
    history = cdb.execute(select(DatasetVersion).where(DatasetVersion.name == d.name, DatasetVersion.layer == d.layer)
                          .order_by(DatasetVersion.created_at.desc()).limit(20)).scalars().all()
    models = cdb.execute(select(MlModel.name, ModelVersion.version, ModelVersion.status).join(ModelVersion, ModelVersion.model_id == MlModel.id)
                         .where(ModelVersion.dataset_versions.contains([str(d.id)]))).all()
    run = cdb.get(PipelineRun, d.run_id) if d.run_id else None
    return {"id": str(d.id), "name": d.name, "layer": d.layer, "source_key": d.source_key, "rows": d.rows, "columns": d.columns, "bytes": d.bytes,
            "fingerprint": d.fingerprint, "quality_status": d.quality_status, "created_at": d.created_at, "is_current": d.is_current,
            "schema": d.schema_json, "profile": d.profile,
            "quality": [{"check": c.check_name, "column": c.column, "status": c.status, "observed": c.observed, "threshold": c.threshold,
                         "details": c.details} for c in checks],
            "upstream": [{"id": str(x.id), "name": x.name, "layer": x.layer, "transform": t} for x, t in up],
            "downstream": [{"id": str(x.id), "name": x.name, "layer": x.layer, "transform": t} for x, t in down],
            "versions": [{"id": str(v.id), "fingerprint": v.fingerprint[:16], "rows": v.rows, "created_at": v.created_at, "is_current": v.is_current,
                          "quality_status": v.quality_status} for v in history],
            "models": [{"name": n, "version": v, "status": s} for n, v, s in models],
            "run": None if run is None else {"id": str(run.id), "pipeline": run.pipeline, "status": run.status, "engine": run.engine,
                                             "finished_at": run.finished_at}}


@router.get("/lineage")
def lineage(p: Principal = Depends(require("data.read")), cdb: Session = Depends(control_db)) -> dict[str, Any]:
    cur = {d.id: d for d in cdb.execute(select(DatasetVersion).where(DatasetVersion.is_current.is_(True))).scalars()}
    edges = cdb.execute(select(LineageEdge).where(LineageEdge.downstream_id.in_(list(cur)), LineageEdge.upstream_id.in_(list(cur)))).scalars().all()
    nodes = [{"id": str(d.id), "kind": "dataset", "label": d.name, "layer": d.layer, "source_key": d.source_key, "rows": d.rows,
              "quality_status": d.quality_status} for d in cur.values()]
    seen_e = set()
    out_edges = []
    for e in edges:
        k = (e.upstream_id, e.downstream_id)
        if k in seen_e:
            continue
        seen_e.add(k)
        out_edges.append({"source": str(e.upstream_id), "target": str(e.downstream_id), "label": e.transform})
    for name, version, status, ds in cdb.execute(select(MlModel.name, ModelVersion.version, ModelVersion.status, ModelVersion.dataset_versions)
                                                 .join(ModelVersion, ModelVersion.model_id == MlModel.id)
                                                 .where(ModelVersion.status.in_(["production", "approved", "candidate"]))).all():
        linked = [x for x in ds if x and uuid.UUID(x) in cur]
        if not linked:
            continue
        mid = f"model:{name}:v{version}"
        nodes.append({"id": mid, "kind": "model", "label": f"{name} v{version}", "layer": "model", "status": status})
        out_edges += [{"source": x, "target": mid, "label": "training"} for x in linked]
    srcs = {d.source_key for d in cur.values() if d.source_key}
    for s in cdb.execute(select(DataSource).where(DataSource.key.in_(srcs))).scalars():
        nodes.append({"id": f"source:{s.key}", "kind": "source", "label": s.name, "layer": "source", "license": s.license})
    for d in cur.values():
        if d.layer == "raw" and d.source_key:
            out_edges.append({"source": f"source:{d.source_key}", "target": str(d.id), "label": "ingestion"})
    return {"nodes": nodes, "edges": out_edges}


@router.get("/pipelines/runs")
def pipeline_runs(p: Principal = Depends(require("data.read")), cdb: Session = Depends(control_db),
                  pipeline: str | None = Query(default=None, max_length=80), limit: int = Query(default=50, le=200)) -> list[dict[str, Any]]:
    q = select(PipelineRun).where(PipelineRun.org_id.is_(None) | (PipelineRun.org_id == p.org_id))
    if pipeline:
        q = q.where(PipelineRun.pipeline.like(f"{pipeline}%"))
    runs = cdb.execute(q.order_by(PipelineRun.created_at.desc()).limit(limit)).scalars().all()
    steps: dict[uuid.UUID, list[PipelineStep]] = {}
    if runs:
        for s in cdb.execute(select(PipelineStep).where(PipelineStep.run_id.in_([r.id for r in runs])).order_by(PipelineStep.started_at)).scalars():
            steps.setdefault(s.run_id, []).append(s)
    return [{"id": str(r.id), "pipeline": r.pipeline, "status": r.status, "engine": r.engine, "started_at": r.started_at, "finished_at": r.finished_at,
             "attempts": r.attempts, "error": r.error, "triggered_by": r.triggered_by, "summary": r.summary,
             "steps": [{"name": s.name, "status": s.status, "rows_in": s.rows_in, "rows_out": s.rows_out, "started_at": s.started_at,
                        "finished_at": s.finished_at, "error": s.error, "duration_s": (s.details or {}).get("duration_s")} for s in steps.get(r.id, [])]}
            for r in runs]


# ------------------------------------------------------------------------------------------------
# Tenant uploads
# ------------------------------------------------------------------------------------------------
def _kind(dtype: pl.DataType) -> str:
    if dtype.is_numeric():
        return "integer" if dtype.is_integer() else "numeric"
    if dtype.is_temporal():
        return "datetime"
    return "string"


def profile_upload(df: pl.DataFrame, template: str) -> tuple[list[dict[str, Any]], dict[str, Any], list[dict[str, Any]], str]:
    for c in df.columns:  # best-effort datetime parsing for string columns named like timestamps
        if df[c].dtype == pl.String and c.lower() in ("ts", "date", "timestamp", "datetime"):
            parsed = df[c].str.to_datetime(strict=False)
            if parsed.null_count() <= df[c].null_count():
                df = df.with_columns(parsed.alias(c))
    schema = [{"name": c, "dtype": str(df[c].dtype), "kind": _kind(df[c].dtype)} for c in df.columns]
    prof = lake.profile(df)
    checks: list[dict[str, Any]] = []
    req = TEMPLATES[template]["required"]
    for col, kind in req.items():
        if col not in df.columns:
            checks.append({"check": "required_column", "column": col, "status": "fail", "detail": "colonne absente"})
            continue
        actual = _kind(df[col].dtype)
        ok = actual == kind or (kind == "numeric" and actual == "integer")
        checks.append({"check": "column_type", "column": col, "status": "pass" if ok else "fail", "detail": f"attendu {kind}, trouvé {actual}"})
    for c in df.columns:
        rate = df[c].null_count() / max(df.height, 1)
        if rate > 0:
            checks.append({"check": "null_rate", "column": c, "status": "warn" if rate < 0.2 else "fail", "detail": f"{rate:.1%} de valeurs manquantes"})
    dup = df.height - df.unique().height
    checks.append({"check": "duplicate_rows", "column": None, "status": "pass" if dup == 0 else "warn", "detail": f"{dup} ligne(s) dupliquée(s)"})
    if "amount" in df.columns and df["amount"].dtype.is_numeric():
        neg = int((df["amount"] < 0).sum())
        checks.append({"check": "amount_non_negative", "column": "amount", "status": "pass" if neg == 0 else "warn", "detail": f"{neg} montant(s) négatif(s)"})
    status = "fail" if any(c["status"] == "fail" for c in checks) else "warn" if any(c["status"] == "warn" for c in checks) else "pass"
    return schema, prof, checks, status


@router.get("/uploads")
def uploads(p: Principal = Depends(require("data.read")), db: Session = Depends(tenant_db)) -> dict[str, Any]:
    rows = db.execute(select(OrgDataset).order_by(OrgDataset.created_at.desc()).limit(100)).scalars().all()
    return {"templates": [{"key": k, "label": v["label"], "required": v["required"]} for k, v in TEMPLATES.items()],
            "items": [{"id": str(d.id), "name": d.name, "fmt": d.fmt, "rows": d.rows, "columns": d.columns, "quality_status": d.quality_status,
                       "created_at": d.created_at} for d in rows]}


@router.get("/uploads/{dataset_id}")
def upload_detail(dataset_id: uuid.UUID, p: Principal = Depends(require("data.read")), db: Session = Depends(tenant_db)) -> dict[str, Any]:
    d = db.execute(select(OrgDataset).where(OrgDataset.id == dataset_id, OrgDataset.org_id == p.org_id)).scalar_one_or_none()
    if d is None:
        raise NotFound("Dataset not found")
    return {"id": str(d.id), "name": d.name, "fmt": d.fmt, "rows": d.rows, "columns": d.columns, "schema": d.schema_json, "profile": d.profile,
            "quality": d.quality, "quality_status": d.quality_status, "created_at": d.created_at, "sha256": d.sha256}


@router.post("/uploads", status_code=201)
async def upload(request: Request, file: UploadFile = File(...), template: str = Form(default="generic"),
                 p: Principal = Depends(require("data.manage")), db: Session = Depends(tenant_db)) -> dict[str, Any]:
    if template not in TEMPLATES:
        raise ValidationFailed("Unknown template")
    max_bytes = get_settings().max_upload_bytes
    data = await file.read(max_bytes + 1)
    if not data or len(data) > max_bytes:
        raise ValidationFailed("Empty or too large file (15 MB max)")
    name = (file.filename or "dataset.csv").replace("/", "_")[:200]
    ext = name.rsplit(".", 1)[-1].lower()
    try:
        if ext == "csv":
            df = pl.read_csv(io.BytesIO(data), infer_schema_length=5000, try_parse_dates=True)
        elif ext == "parquet":
            df = pl.read_parquet(io.BytesIO(data))
        else:
            raise ValidationFailed("CSV or Parquet only", code="unsupported_type")
    except ValidationFailed:
        raise
    except Exception as exc:
        raise ValidationFailed(f"Unreadable file: {type(exc).__name__}", code="unreadable") from exc
    if df.height == 0 or df.width == 0 or df.width > 300:
        raise ValidationFailed("The file must contain 1–300 columns and at least one row")
    sha = hashlib.sha256(data).hexdigest()
    if db.execute(select(OrgDataset.id).where(OrgDataset.sha256 == sha)).first():
        raise Conflict("This file has already been uploaded", code="duplicate_dataset")
    schema, prof, checks, status = profile_upload(df, template)
    did = uuid.uuid4()
    key = org_key(p.org_id, "datasets", str(did), name)
    get_store().put(key, data, "text/csv" if ext == "csv" else "application/octet-stream")
    d = OrgDataset(id=did, org_id=p.org_id, name=name, fmt=ext, storage_key=key, sha256=sha, rows=df.height, columns=df.width, schema_json=schema,
                   profile=prof, quality=checks, quality_status=status, uploaded_by=p.actor_id)
    db.add(d)
    db.flush()
    audit.record("data.uploaded", org_id=p.org_id, actor_user_id=p.user_id, actor_label=p.label, ip=client_ip(request), resource_type="org_dataset",
                 resource_id=str(d.id), details={"name": name, "rows": df.height, "template": template, "quality": status})
    return upload_detail(d.id, p, db)
