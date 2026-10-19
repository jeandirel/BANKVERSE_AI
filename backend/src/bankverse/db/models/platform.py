"""Platform models (schema ``platform``): data catalogue, pipelines, lineage, model registry and
public reference data shared by every tenant (read-only for tenants)."""

from __future__ import annotations

import uuid
from datetime import date, datetime
from typing import Any

from pgvector.sqlalchemy import Vector
from sqlalchemy import (
    BigInteger,
    Boolean,
    Computed,
    Date,
    DateTime,
    Float,
    ForeignKey,
    Index,
    Integer,
    String,
    Text,
    UniqueConstraint,
)
from sqlalchemy.dialects.postgresql import JSONB, TSVECTOR, UUID
from sqlalchemy.orm import Mapped, mapped_column, relationship

from bankverse.db.base import ControlBase, Timestamped, UUIDPk

SCHEMA = {"schema": "platform"}
EMBED_DIM = 256


class DataSource(Timestamped, ControlBase):
    """Data registry entry (one per external source or synthetic generator)."""

    __tablename__ = "data_sources"
    __table_args__ = SCHEMA

    key: Mapped[str] = mapped_column(String(64), primary_key=True)
    name: Mapped[str] = mapped_column(String(200), nullable=False)
    owner: Mapped[str] = mapped_column(String(200), nullable=False)
    kind: Mapped[str] = mapped_column(String(32), nullable=False)  # real_public | public_synthetic | demo_synthetic
    license: Mapped[str] = mapped_column(String(120), nullable=False)
    restrictions: Mapped[str] = mapped_column(Text, nullable=False, default="")
    redistribution: Mapped[str] = mapped_column(String(200), nullable=False, default="")
    original_url: Mapped[str] = mapped_column(Text, nullable=False)
    access_url: Mapped[str | None] = mapped_column(Text)
    modules: Mapped[list[str]] = mapped_column(JSONB, nullable=False, default=list)
    access_status: Mapped[str] = mapped_column(String(32), nullable=False, default="unknown")
    access_note: Mapped[str] = mapped_column(Text, nullable=False, default="")
    commercial_use_ok: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)
    meta: Mapped[dict[str, Any]] = mapped_column(JSONB, nullable=False, default=dict)


class DatasetVersion(UUIDPk, Timestamped, ControlBase):
    __tablename__ = "dataset_versions"
    __table_args__ = (UniqueConstraint("name", "layer", "fingerprint", name="uq_dataset_version"), SCHEMA)

    source_key: Mapped[str | None] = mapped_column(ForeignKey("platform.data_sources.key"))
    name: Mapped[str] = mapped_column(String(120), nullable=False, index=True)
    layer: Mapped[str] = mapped_column(String(16), nullable=False)  # raw | bronze | silver | gold | features
    path: Mapped[str] = mapped_column(Text, nullable=False)
    fingerprint: Mapped[str] = mapped_column(String(64), nullable=False)  # sha256 of content / manifest
    rows: Mapped[int | None] = mapped_column(BigInteger)
    columns: Mapped[int | None] = mapped_column(Integer)
    bytes: Mapped[int | None] = mapped_column(BigInteger)
    schema_json: Mapped[list[dict[str, Any]]] = mapped_column(JSONB, nullable=False, default=list)
    profile: Mapped[dict[str, Any]] = mapped_column(JSONB, nullable=False, default=dict)
    quality_status: Mapped[str] = mapped_column(String(16), nullable=False, default="unchecked")
    collected_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    run_id: Mapped[uuid.UUID | None] = mapped_column(UUID(as_uuid=True))
    is_current: Mapped[bool] = mapped_column(Boolean, nullable=False, default=True)


class QualityResult(UUIDPk, Timestamped, ControlBase):
    __tablename__ = "quality_results"
    __table_args__ = SCHEMA

    dataset_version_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("platform.dataset_versions.id", ondelete="CASCADE"), index=True
    )
    check_name: Mapped[str] = mapped_column(String(120), nullable=False)
    column: Mapped[str | None] = mapped_column(String(120))
    status: Mapped[str] = mapped_column(String(16), nullable=False)  # pass | warn | fail
    observed: Mapped[float | None] = mapped_column(Float)
    threshold: Mapped[float | None] = mapped_column(Float)
    details: Mapped[dict[str, Any]] = mapped_column(JSONB, nullable=False, default=dict)


class PipelineRun(UUIDPk, Timestamped, ControlBase):
    __tablename__ = "pipeline_runs"
    __table_args__ = SCHEMA

    pipeline: Mapped[str] = mapped_column(String(80), nullable=False, index=True)
    status: Mapped[str] = mapped_column(String(16), nullable=False, default="running")  # running|succeeded|failed|skipped
    engine: Mapped[str] = mapped_column(String(16), nullable=False, default="polars")  # polars|spark|duckdb|python
    params: Mapped[dict[str, Any]] = mapped_column(JSONB, nullable=False, default=dict)
    started_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    finished_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    attempts: Mapped[int] = mapped_column(Integer, nullable=False, default=1)
    error: Mapped[str | None] = mapped_column(Text)
    triggered_by: Mapped[str] = mapped_column(String(120), nullable=False, default="cli")
    org_id: Mapped[uuid.UUID | None] = mapped_column(UUID(as_uuid=True), index=True)
    summary: Mapped[dict[str, Any]] = mapped_column(JSONB, nullable=False, default=dict)

    steps: Mapped[list[PipelineStep]] = relationship(
        back_populates="run", cascade="all, delete-orphan", order_by="PipelineStep.started_at"
    )


class PipelineStep(UUIDPk, ControlBase):
    __tablename__ = "pipeline_steps"
    __table_args__ = SCHEMA

    run_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("platform.pipeline_runs.id", ondelete="CASCADE"), index=True)
    name: Mapped[str] = mapped_column(String(120), nullable=False)
    status: Mapped[str] = mapped_column(String(16), nullable=False)
    rows_in: Mapped[int | None] = mapped_column(BigInteger)
    rows_out: Mapped[int | None] = mapped_column(BigInteger)
    started_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    finished_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    error: Mapped[str | None] = mapped_column(Text)
    details: Mapped[dict[str, Any]] = mapped_column(JSONB, nullable=False, default=dict)

    run: Mapped[PipelineRun] = relationship(back_populates="steps")


class LineageEdge(UUIDPk, Timestamped, ControlBase):
    __tablename__ = "lineage_edges"
    __table_args__ = (
        UniqueConstraint("upstream_id", "downstream_id", name="uq_lineage_edge"),
        SCHEMA,
    )

    upstream_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("platform.dataset_versions.id", ondelete="CASCADE"))
    downstream_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("platform.dataset_versions.id", ondelete="CASCADE"))
    transform: Mapped[str] = mapped_column(String(200), nullable=False)
    run_id: Mapped[uuid.UUID | None] = mapped_column(UUID(as_uuid=True))


# ------------------------------------------------------------------------------------------------
# Model registry & governance
# ------------------------------------------------------------------------------------------------
class MlModel(UUIDPk, Timestamped, ControlBase):
    __tablename__ = "ml_models"
    __table_args__ = SCHEMA

    name: Mapped[str] = mapped_column(String(120), unique=True, nullable=False)
    module: Mapped[str] = mapped_column(String(32), nullable=False, index=True)
    task: Mapped[str] = mapped_column(String(64), nullable=False)
    description: Mapped[str] = mapped_column(Text, nullable=False, default="")
    owner: Mapped[str] = mapped_column(String(120), nullable=False, default="BANKVERSE AI data science")
    risk_tier: Mapped[str] = mapped_column(String(16), nullable=False, default="high")  # AI Act style tiering (internal)

    versions: Mapped[list[ModelVersion]] = relationship(back_populates="model", order_by="ModelVersion.version")


class ModelVersion(UUIDPk, Timestamped, ControlBase):
    __tablename__ = "model_versions"
    __table_args__ = (UniqueConstraint("model_id", "version", name="uq_model_version"), SCHEMA)

    model_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("platform.ml_models.id", ondelete="CASCADE"), index=True)
    version: Mapped[int] = mapped_column(Integer, nullable=False)
    algorithm: Mapped[str] = mapped_column(String(80), nullable=False)
    status: Mapped[str] = mapped_column(String(16), nullable=False, default="candidate")
    # candidate -> approved -> production -> archived ; or rejected
    metrics: Mapped[dict[str, Any]] = mapped_column(JSONB, nullable=False, default=dict)
    params: Mapped[dict[str, Any]] = mapped_column(JSONB, nullable=False, default=dict)
    features: Mapped[list[str]] = mapped_column(JSONB, nullable=False, default=list)
    dataset_versions: Mapped[list[str]] = mapped_column(JSONB, nullable=False, default=list)
    reference_profile: Mapped[dict[str, Any]] = mapped_column(JSONB, nullable=False, default=dict)
    artifact_path: Mapped[str] = mapped_column(Text, nullable=False)
    artifact_sha256: Mapped[str] = mapped_column(String(64), nullable=False)
    mlflow_run_id: Mapped[str | None] = mapped_column(String(64))
    git_commit: Mapped[str | None] = mapped_column(String(64))
    trained_by: Mapped[str] = mapped_column(String(120), nullable=False, default="pipeline")
    training_seconds: Mapped[float | None] = mapped_column(Float)
    model_card: Mapped[dict[str, Any]] = mapped_column(JSONB, nullable=False, default=dict)
    is_champion_candidate: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)

    model: Mapped[MlModel] = relationship(back_populates="versions")


class ModelApproval(UUIDPk, Timestamped, ControlBase):
    __tablename__ = "model_approvals"
    __table_args__ = SCHEMA

    model_version_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("platform.model_versions.id", ondelete="CASCADE"), index=True
    )
    decision: Mapped[str] = mapped_column(String(16), nullable=False)  # approved | rejected
    reviewer_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), nullable=False)
    reviewer_label: Mapped[str] = mapped_column(String(320), nullable=False)
    comment: Mapped[str] = mapped_column(Text, nullable=False)


class ModelDeployment(UUIDPk, Timestamped, ControlBase):
    __tablename__ = "model_deployments"
    __table_args__ = SCHEMA

    model_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("platform.ml_models.id", ondelete="CASCADE"), index=True)
    model_version_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("platform.model_versions.id", ondelete="CASCADE"))
    action: Mapped[str] = mapped_column(String(16), nullable=False)  # deploy | rollback | retire
    previous_version_id: Mapped[uuid.UUID | None] = mapped_column(UUID(as_uuid=True))
    actor_label: Mapped[str] = mapped_column(String(320), nullable=False)
    reason: Mapped[str] = mapped_column(Text, nullable=False, default="")
    active: Mapped[bool] = mapped_column(Boolean, nullable=False, default=True)


# ------------------------------------------------------------------------------------------------
# Public reference data (macro-financial series, public financial-report corpus)
# ------------------------------------------------------------------------------------------------
class RefSeries(ControlBase):
    __tablename__ = "ref_series"
    __table_args__ = SCHEMA

    key: Mapped[str] = mapped_column(String(64), primary_key=True)
    name: Mapped[str] = mapped_column(String(200), nullable=False)
    source_key: Mapped[str] = mapped_column(ForeignKey("platform.data_sources.key"))
    upstream: Mapped[str] = mapped_column(String(200), nullable=False, default="")
    frequency: Mapped[str] = mapped_column(String(8), nullable=False)  # D | M | Q | A
    unit: Mapped[str] = mapped_column(String(64), nullable=False, default="")
    description: Mapped[str] = mapped_column(Text, nullable=False, default="")
    first_date: Mapped[date | None] = mapped_column(Date)
    last_date: Mapped[date | None] = mapped_column(Date)
    n_obs: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    dataset_version_id: Mapped[uuid.UUID | None] = mapped_column(UUID(as_uuid=True))


class RefObservation(ControlBase):
    __tablename__ = "ref_observations"
    __table_args__ = SCHEMA

    series_key: Mapped[str] = mapped_column(ForeignKey("platform.ref_series.key", ondelete="CASCADE"), primary_key=True)
    obs_date: Mapped[date] = mapped_column(Date, primary_key=True)
    value: Mapped[float] = mapped_column(Float, nullable=False)


class PublicDocument(UUIDPk, Timestamped, ControlBase):
    """A public financial-report page (e.g. FinQA source page of a 10-K filing)."""

    __tablename__ = "public_documents"
    __table_args__ = SCHEMA

    source_key: Mapped[str] = mapped_column(ForeignKey("platform.data_sources.key"))
    doc_ref: Mapped[str] = mapped_column(String(200), unique=True, nullable=False)  # e.g. ADI/2009/page_49.pdf
    company: Mapped[str] = mapped_column(String(32), nullable=False, index=True)
    fiscal_year: Mapped[int | None] = mapped_column(Integer, index=True)
    page: Mapped[int | None] = mapped_column(Integer)
    title: Mapped[str] = mapped_column(String(300), nullable=False)
    language: Mapped[str] = mapped_column(String(5), nullable=False, default="en")


class PublicChunk(UUIDPk, ControlBase):
    __tablename__ = "public_chunks"
    __table_args__ = (
        Index("ix_public_chunks_tsv", "tsv", postgresql_using="gin"),
        SCHEMA,
    )

    document_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("platform.public_documents.id", ondelete="CASCADE"), index=True
    )
    ordinal: Mapped[int] = mapped_column(Integer, nullable=False)
    kind: Mapped[str] = mapped_column(String(16), nullable=False)  # text | table_row | table
    content: Mapped[str] = mapped_column(Text, nullable=False)
    tsv: Mapped[Any] = mapped_column(TSVECTOR, Computed("to_tsvector('english', content)", persisted=True))
    embedding: Mapped[Any] = mapped_column(Vector(EMBED_DIM), nullable=True)
    meta: Mapped[dict[str, Any]] = mapped_column(JSONB, nullable=False, default=dict)
