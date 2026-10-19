"""Tenant business data (schema ``bank``). Every table is organisation-scoped and protected by a
FORCED row-level-security policy ``org_id = current_setting('app.org_id')::uuid`` (see migration)."""

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
    Numeric,
    String,
    Text,
    UniqueConstraint,
)
from sqlalchemy.dialects.postgresql import JSONB, TSVECTOR, UUID
from sqlalchemy.orm import Mapped, mapped_column

from bankverse.db.base import TenantBase, TenantScoped, Timestamped, UUIDPk

EMBED_DIM = 256


# ------------------------------------------------------------------------------------------------
# Core banking entities (DEMO bank data is generated; pilots would map real core-banking extracts)
# ------------------------------------------------------------------------------------------------
class Customer(UUIDPk, TenantScoped, Timestamped, TenantBase):
    __tablename__ = "customers"
    __table_args__ = (UniqueConstraint("org_id", "external_ref", name="uq_customer_ref"),)

    external_ref: Mapped[str] = mapped_column(String(64), nullable=False)
    full_name: Mapped[str] = mapped_column(String(200), nullable=False)
    customer_type: Mapped[str] = mapped_column(String(16), nullable=False, default="individual")  # individual|business
    segment: Mapped[str] = mapped_column(String(32), nullable=False, default="retail")
    birth_year: Mapped[int | None] = mapped_column(Integer)
    country: Mapped[str] = mapped_column(String(2), nullable=False, default="FR")
    city: Mapped[str | None] = mapped_column(String(120))
    postal_code: Mapped[str | None] = mapped_column(String(16))
    occupation: Mapped[str | None] = mapped_column(String(120))
    annual_income: Mapped[float | None] = mapped_column(Float)
    risk_rating: Mapped[str] = mapped_column(String(16), nullable=False, default="standard")  # low|standard|high
    kyc_status: Mapped[str] = mapped_column(String(16), nullable=False, default="verified")
    pep: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)
    onboarded_at: Mapped[date | None] = mapped_column(Date)
    is_demo: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)


class Account(UUIDPk, TenantScoped, Timestamped, TenantBase):
    __tablename__ = "accounts"
    __table_args__ = (UniqueConstraint("org_id", "iban", name="uq_account_iban"),)

    customer_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("bank.customers.id", ondelete="CASCADE"), index=True)
    iban: Mapped[str] = mapped_column(String(34), nullable=False)
    account_type: Mapped[str] = mapped_column(String(24), nullable=False, default="current")
    currency: Mapped[str] = mapped_column(String(3), nullable=False, default="EUR")
    opened_at: Mapped[date | None] = mapped_column(Date)
    balance: Mapped[float] = mapped_column(Numeric(18, 2), nullable=False, default=0)
    status: Mapped[str] = mapped_column(String(16), nullable=False, default="active")
    is_demo: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)


class Card(UUIDPk, TenantScoped, Timestamped, TenantBase):
    __tablename__ = "cards"

    account_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("bank.accounts.id", ondelete="CASCADE"), index=True)
    customer_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("bank.customers.id", ondelete="CASCADE"), index=True)
    card_ref: Mapped[str] = mapped_column(String(32), nullable=False)  # masked PAN-like reference, never a real PAN
    card_index: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    brand: Mapped[str] = mapped_column(String(16), nullable=False, default="visa")
    card_type: Mapped[str] = mapped_column(String(16), nullable=False, default="debit")
    credit_limit: Mapped[float | None] = mapped_column(Float)
    has_chip: Mapped[bool] = mapped_column(Boolean, nullable=False, default=True)
    status: Mapped[str] = mapped_column(String(16), nullable=False, default="active")
    home_zip: Mapped[str | None] = mapped_column(String(10))
    home_state: Mapped[str | None] = mapped_column(String(32))
    is_demo: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)


class CardTransaction(UUIDPk, TenantScoped, Timestamped, TenantBase):
    """Card transaction stream — schema compatible with the IBM TabFormer training data."""

    __tablename__ = "card_transactions"
    __table_args__ = (
        Index("ix_cardtx_org_ts", "org_id", "ts"),
        Index("ix_cardtx_card_ts", "card_id", "ts"),
        Index("ix_cardtx_org_score", "org_id", "risk_score"),
    )

    card_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("bank.cards.id", ondelete="CASCADE"))
    customer_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("bank.customers.id", ondelete="CASCADE"), index=True)
    ts: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    amount: Mapped[float] = mapped_column(Float, nullable=False)
    currency: Mapped[str] = mapped_column(String(3), nullable=False, default="EUR")
    use_chip: Mapped[str] = mapped_column(String(24), nullable=False)  # Chip Transaction|Swipe Transaction|Online Transaction
    merchant_id: Mapped[str] = mapped_column(String(32), nullable=False)
    merchant_name: Mapped[str | None] = mapped_column(String(120))
    merchant_city: Mapped[str | None] = mapped_column(String(120))
    merchant_state: Mapped[str | None] = mapped_column(String(64))
    merchant_zip: Mapped[str | None] = mapped_column(String(10))
    mcc: Mapped[int] = mapped_column(Integer, nullable=False)
    errors: Mapped[str | None] = mapped_column(String(120))
    risk_score: Mapped[float | None] = mapped_column(Float)
    scored_model_version: Mapped[str | None] = mapped_column(String(64))
    scored_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    # Ground truth of the DEMO simulator scenario. Never used as a model feature; only shown as
    # "simulation label" for the demo and to compute demo-only operational statistics.
    sim_scenario: Mapped[str | None] = mapped_column(String(48))
    sim_is_fraud: Mapped[bool | None] = mapped_column(Boolean)
    is_demo: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)
    source: Mapped[str] = mapped_column(String(16), nullable=False, default="stream")  # stream|simulator|import


class Transfer(UUIDPk, TenantScoped, Timestamped, TenantBase):
    """Account-to-account and external transfers (AML graph source)."""

    __tablename__ = "transfers"
    __table_args__ = (
        Index("ix_transfer_org_ts", "org_id", "ts"),
        Index("ix_transfer_src", "src_account_id", "ts"),
        Index("ix_transfer_dst", "dst_account_id", "ts"),
    )

    src_account_id: Mapped[uuid.UUID | None] = mapped_column(ForeignKey("bank.accounts.id", ondelete="CASCADE"))
    dst_account_id: Mapped[uuid.UUID | None] = mapped_column(ForeignKey("bank.accounts.id", ondelete="CASCADE"))
    counterparty_iban: Mapped[str | None] = mapped_column(String(34))
    counterparty_name: Mapped[str | None] = mapped_column(String(200))
    counterparty_country: Mapped[str | None] = mapped_column(String(2))
    ts: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    amount: Mapped[float] = mapped_column(Float, nullable=False)
    currency: Mapped[str] = mapped_column(String(3), nullable=False, default="EUR")
    channel: Mapped[str] = mapped_column(String(16), nullable=False, default="sepa")  # sepa|instant|swift|cash
    direction: Mapped[str] = mapped_column(String(8), nullable=False, default="internal")  # internal|in|out
    reference: Mapped[str | None] = mapped_column(String(140))
    sim_typology: Mapped[str | None] = mapped_column(String(48))
    is_demo: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)


# ------------------------------------------------------------------------------------------------
# Fraud
# ------------------------------------------------------------------------------------------------
class FraudAlert(UUIDPk, TenantScoped, Timestamped, TenantBase):
    __tablename__ = "fraud_alerts"
    __table_args__ = (
        UniqueConstraint("org_id", "transaction_id", name="uq_fraud_alert_tx"),
        Index("ix_fraud_alert_status", "org_id", "status", "priority"),
    )

    transaction_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("bank.card_transactions.id", ondelete="CASCADE"))
    customer_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("bank.customers.id", ondelete="CASCADE"), index=True)
    score: Mapped[float] = mapped_column(Float, nullable=False)
    risk_band: Mapped[str] = mapped_column(String(16), nullable=False)  # critical|high|medium
    priority: Mapped[int] = mapped_column(Integer, nullable=False)  # 1 = highest
    expected_loss: Mapped[float] = mapped_column(Float, nullable=False, default=0)
    model_version: Mapped[str] = mapped_column(String(64), nullable=False)
    reasons: Mapped[list[dict[str, Any]]] = mapped_column(JSONB, nullable=False, default=list)
    status: Mapped[str] = mapped_column(String(24), nullable=False, default="new")
    # new | in_review | escalated | closed_fraud | closed_legitimate
    assigned_to: Mapped[uuid.UUID | None] = mapped_column(UUID(as_uuid=True))
    sla_due_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    resolved_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    resolution_note: Mapped[str | None] = mapped_column(Text)
    analyst_label: Mapped[str | None] = mapped_column(String(16))  # fraud | legitimate (feedback loop)
    case_id: Mapped[uuid.UUID | None] = mapped_column(UUID(as_uuid=True))


# ------------------------------------------------------------------------------------------------
# AML
# ------------------------------------------------------------------------------------------------
class AmlAccountScore(UUIDPk, TenantScoped, Timestamped, TenantBase):
    __tablename__ = "aml_account_scores"
    __table_args__ = (UniqueConstraint("org_id", "account_id", "model_version", name="uq_aml_score"),)

    account_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("bank.accounts.id", ondelete="CASCADE"), index=True)
    model_version: Mapped[str] = mapped_column(String(64), nullable=False)
    score: Mapped[float] = mapped_column(Float, nullable=False)
    anomaly_score: Mapped[float | None] = mapped_column(Float)
    features: Mapped[dict[str, Any]] = mapped_column(JSONB, nullable=False, default=dict)
    community_id: Mapped[int | None] = mapped_column(Integer)


class AmlAlert(UUIDPk, TenantScoped, Timestamped, TenantBase):
    __tablename__ = "aml_alerts"
    __table_args__ = (Index("ix_aml_alert_status", "org_id", "status", "score"),)

    account_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("bank.accounts.id", ondelete="CASCADE"), index=True)
    customer_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("bank.customers.id", ondelete="CASCADE"), index=True)
    score: Mapped[float] = mapped_column(Float, nullable=False)
    risk_band: Mapped[str] = mapped_column(String(16), nullable=False)
    typologies: Mapped[list[str]] = mapped_column(JSONB, nullable=False, default=list)
    rule_hits: Mapped[list[dict[str, Any]]] = mapped_column(JSONB, nullable=False, default=list)
    model_scores: Mapped[dict[str, Any]] = mapped_column(JSONB, nullable=False, default=dict)
    evidence_transfer_ids: Mapped[list[str]] = mapped_column(JSONB, nullable=False, default=list)
    status: Mapped[str] = mapped_column(String(24), nullable=False, default="new")
    # new | investigating | escalated | closed_no_action | closed_reported
    assigned_to: Mapped[uuid.UUID | None] = mapped_column(UUID(as_uuid=True))
    case_id: Mapped[uuid.UUID | None] = mapped_column(UUID(as_uuid=True))
    model_version: Mapped[str] = mapped_column(String(64), nullable=False)
    resolved_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))


# ------------------------------------------------------------------------------------------------
# Investigation cases (shared by fraud, AML, credit, KYC)
# ------------------------------------------------------------------------------------------------
class Case(UUIDPk, TenantScoped, Timestamped, TenantBase):
    __tablename__ = "cases"
    __table_args__ = (UniqueConstraint("org_id", "reference", name="uq_case_reference"),)

    reference: Mapped[str] = mapped_column(String(32), nullable=False)
    case_type: Mapped[str] = mapped_column(String(16), nullable=False)  # fraud|aml|credit|kyc
    title: Mapped[str] = mapped_column(String(300), nullable=False)
    status: Mapped[str] = mapped_column(String(24), nullable=False, default="open")
    # open | investigating | pending_decision | closed
    priority: Mapped[str] = mapped_column(String(16), nullable=False, default="medium")
    customer_id: Mapped[uuid.UUID | None] = mapped_column(ForeignKey("bank.customers.id", ondelete="SET NULL"))
    created_by: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), nullable=False)
    assigned_to: Mapped[uuid.UUID | None] = mapped_column(UUID(as_uuid=True))
    decision: Mapped[str | None] = mapped_column(String(48))
    decision_rationale: Mapped[str | None] = mapped_column(Text)
    decided_by: Mapped[uuid.UUID | None] = mapped_column(UUID(as_uuid=True))
    decided_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    closed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    summary: Mapped[str | None] = mapped_column(Text)


class CaseEvent(UUIDPk, TenantScoped, Timestamped, TenantBase):
    __tablename__ = "case_events"

    case_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("bank.cases.id", ondelete="CASCADE"), index=True)
    actor_id: Mapped[uuid.UUID | None] = mapped_column(UUID(as_uuid=True))
    actor_label: Mapped[str] = mapped_column(String(320), nullable=False)
    kind: Mapped[str] = mapped_column(String(24), nullable=False)
    # note | evidence | status_change | link_alert | report | decision | agent
    payload: Mapped[dict[str, Any]] = mapped_column(JSONB, nullable=False, default=dict)


# ------------------------------------------------------------------------------------------------
# Credit
# ------------------------------------------------------------------------------------------------
class CreditApplication(UUIDPk, TenantScoped, Timestamped, TenantBase):
    __tablename__ = "credit_applications"
    __table_args__ = (UniqueConstraint("org_id", "reference", name="uq_credit_ref"),)

    reference: Mapped[str] = mapped_column(String(32), nullable=False)
    customer_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("bank.customers.id", ondelete="CASCADE"), index=True)
    product: Mapped[str] = mapped_column(String(32), nullable=False, default="credit_card")
    requested_limit: Mapped[float] = mapped_column(Float, nullable=False)
    features: Mapped[dict[str, Any]] = mapped_column(JSONB, nullable=False, default=dict)
    status: Mapped[str] = mapped_column(String(24), nullable=False, default="submitted")
    # submitted | scored | under_review | decided
    pd: Mapped[float | None] = mapped_column(Float)
    risk_grade: Mapped[str | None] = mapped_column(String(4))
    model_version: Mapped[str | None] = mapped_column(String(64))
    explanation: Mapped[list[dict[str, Any]]] = mapped_column(JSONB, nullable=False, default=list)
    scored_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    analyst_decision: Mapped[str | None] = mapped_column(String(24))  # approve | decline | request_info
    analyst_rationale: Mapped[str | None] = mapped_column(Text)
    decided_by: Mapped[uuid.UUID | None] = mapped_column(UUID(as_uuid=True))
    decided_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    is_demo: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)


# ------------------------------------------------------------------------------------------------
# Documents (Document AI / KYC)
# ------------------------------------------------------------------------------------------------
class Document(UUIDPk, TenantScoped, Timestamped, TenantBase):
    __tablename__ = "documents"
    __table_args__ = (
        Index("ix_documents_tsv", "tsv", postgresql_using="gin"),
        UniqueConstraint("org_id", "sha256", name="uq_document_sha"),
    )

    filename: Mapped[str] = mapped_column(String(255), nullable=False)
    mime_type: Mapped[str] = mapped_column(String(64), nullable=False)
    size_bytes: Mapped[int] = mapped_column(BigInteger, nullable=False)
    sha256: Mapped[str] = mapped_column(String(64), nullable=False)
    storage_key: Mapped[str] = mapped_column(Text, nullable=False)
    customer_id: Mapped[uuid.UUID | None] = mapped_column(ForeignKey("bank.customers.id", ondelete="SET NULL"))
    doc_type: Mapped[str | None] = mapped_column(String(48))
    doc_type_confidence: Mapped[float | None] = mapped_column(Float)
    status: Mapped[str] = mapped_column(String(24), nullable=False, default="uploaded")
    # uploaded | processing | needs_review | validated | rejected | failed
    page_count: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    ocr_used: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)
    text: Mapped[str | None] = mapped_column(Text)
    tsv: Mapped[Any] = mapped_column(
        TSVECTOR,
        Computed("to_tsvector('simple', coalesce(filename, '') || ' ' || coalesce(text, ''))", persisted=True),
    )
    uploaded_by: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), nullable=False)
    validated_by: Mapped[uuid.UUID | None] = mapped_column(UUID(as_uuid=True))
    validated_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    processing_ms: Mapped[int | None] = mapped_column(Integer)
    error: Mapped[str | None] = mapped_column(Text)
    engine: Mapped[str] = mapped_column(String(32), nullable=False, default="local")  # local | textract
    is_demo: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)


class DocumentPage(UUIDPk, TenantScoped, TenantBase):
    __tablename__ = "document_pages"
    __table_args__ = (UniqueConstraint("document_id", "page_no", name="uq_doc_page"),)

    document_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("bank.documents.id", ondelete="CASCADE"), index=True)
    page_no: Mapped[int] = mapped_column(Integer, nullable=False)
    width: Mapped[float] = mapped_column(Float, nullable=False)
    height: Mapped[float] = mapped_column(Float, nullable=False)
    image_key: Mapped[str | None] = mapped_column(Text)
    ocr_used: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)
    ocr_confidence: Mapped[float | None] = mapped_column(Float)
    text: Mapped[str] = mapped_column(Text, nullable=False, default="")
    words: Mapped[list[dict[str, Any]]] = mapped_column(JSONB, nullable=False, default=list)
    tables: Mapped[list[dict[str, Any]]] = mapped_column(JSONB, nullable=False, default=list)


class DocumentField(UUIDPk, TenantScoped, Timestamped, TenantBase):
    __tablename__ = "document_fields"

    document_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("bank.documents.id", ondelete="CASCADE"), index=True)
    name: Mapped[str] = mapped_column(String(64), nullable=False)
    value: Mapped[str | None] = mapped_column(Text)
    normalized: Mapped[str | None] = mapped_column(Text)
    confidence: Mapped[float] = mapped_column(Float, nullable=False, default=0)
    page_no: Mapped[int | None] = mapped_column(Integer)
    bbox: Mapped[list[float] | None] = mapped_column(JSONB)  # [x0, y0, x1, y1] in page points
    required: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)
    source: Mapped[str] = mapped_column(String(16), nullable=False, default="extracted")  # extracted|corrected
    corrected_by: Mapped[uuid.UUID | None] = mapped_column(UUID(as_uuid=True))
    corrected_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))


class DocumentCheck(UUIDPk, TenantScoped, Timestamped, TenantBase):
    __tablename__ = "document_checks"

    document_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("bank.documents.id", ondelete="CASCADE"), index=True)
    check_name: Mapped[str] = mapped_column(String(64), nullable=False)
    status: Mapped[str] = mapped_column(String(16), nullable=False)  # pass | warn | fail | not_applicable
    detail: Mapped[str] = mapped_column(Text, nullable=False, default="")


# ------------------------------------------------------------------------------------------------
# Customer intelligence
# ------------------------------------------------------------------------------------------------
class CustomerMessage(UUIDPk, TenantScoped, Timestamped, TenantBase):
    __tablename__ = "customer_messages"
    __table_args__ = (Index("ix_custmsg_org_ts", "org_id", "received_at"),)

    customer_id: Mapped[uuid.UUID | None] = mapped_column(ForeignKey("bank.customers.id", ondelete="SET NULL"))
    received_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    channel: Mapped[str] = mapped_column(String(16), nullable=False, default="app")
    text: Mapped[str] = mapped_column(Text, nullable=False)
    predicted_topic: Mapped[str | None] = mapped_column(String(64))
    topic_group: Mapped[str | None] = mapped_column(String(64))
    confidence: Mapped[float | None] = mapped_column(Float)
    model_version: Mapped[str | None] = mapped_column(String(64))
    is_complaint: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)
    embedding: Mapped[Any] = mapped_column(Vector(EMBED_DIM), nullable=True)
    is_demo: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)


class CustomerSegmentAssignment(UUIDPk, TenantScoped, Timestamped, TenantBase):
    __tablename__ = "customer_segments"
    __table_args__ = (UniqueConstraint("org_id", "customer_id", "model_version", name="uq_cust_segment"),)

    customer_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("bank.customers.id", ondelete="CASCADE"), index=True)
    model_version: Mapped[str] = mapped_column(String(64), nullable=False)
    segment_id: Mapped[int] = mapped_column(Integer, nullable=False)
    segment_label: Mapped[str] = mapped_column(String(80), nullable=False)
    features: Mapped[dict[str, Any]] = mapped_column(JSONB, nullable=False, default=dict)


# ------------------------------------------------------------------------------------------------
# Private knowledge base (bank documents indexed for the copilot)
# ------------------------------------------------------------------------------------------------
class KbChunk(UUIDPk, TenantScoped, Timestamped, TenantBase):
    __tablename__ = "kb_chunks"
    __table_args__ = (Index("ix_kb_chunks_tsv", "tsv", postgresql_using="gin"),)

    document_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("bank.documents.id", ondelete="CASCADE"), index=True)
    page_no: Mapped[int | None] = mapped_column(Integer)
    ordinal: Mapped[int] = mapped_column(Integer, nullable=False)
    kind: Mapped[str] = mapped_column(String(16), nullable=False, default="text")
    content: Mapped[str] = mapped_column(Text, nullable=False)
    tsv: Mapped[Any] = mapped_column(TSVECTOR, Computed("to_tsvector('simple', content)", persisted=True))
    embedding: Mapped[Any] = mapped_column(Vector(EMBED_DIM), nullable=True)


class CopilotConversation(UUIDPk, TenantScoped, Timestamped, TenantBase):
    __tablename__ = "copilot_conversations"

    user_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), nullable=False, index=True)
    title: Mapped[str] = mapped_column(String(300), nullable=False)
    updated_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))


class CopilotMessage(UUIDPk, TenantScoped, Timestamped, TenantBase):
    __tablename__ = "copilot_messages"

    conversation_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("bank.copilot_conversations.id", ondelete="CASCADE"), index=True
    )
    role: Mapped[str] = mapped_column(String(16), nullable=False)  # user | assistant
    content: Mapped[str] = mapped_column(Text, nullable=False)
    citations: Mapped[list[dict[str, Any]]] = mapped_column(JSONB, nullable=False, default=list)
    calculation: Mapped[dict[str, Any] | None] = mapped_column(JSONB)
    status: Mapped[str] = mapped_column(String(24), nullable=False, default="answered")
    # answered | insufficient_evidence | refused | blocked | unavailable
    mode: Mapped[str] = mapped_column(String(24), nullable=False, default="extractive")  # extractive | llm
    latency_ms: Mapped[int | None] = mapped_column(Integer)
    meta: Mapped[dict[str, Any]] = mapped_column(JSONB, nullable=False, default=dict)


# ------------------------------------------------------------------------------------------------
# Agentic AI
# ------------------------------------------------------------------------------------------------
class AgentRun(UUIDPk, TenantScoped, Timestamped, TenantBase):
    __tablename__ = "agent_runs"

    user_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), nullable=False, index=True)
    request: Mapped[str] = mapped_column(Text, nullable=False)
    agent: Mapped[str | None] = mapped_column(String(48))
    status: Mapped[str] = mapped_column(String(24), nullable=False, default="queued")
    # queued | running | awaiting_approval | completed | failed | blocked | rejected
    result: Mapped[dict[str, Any] | None] = mapped_column(JSONB)
    error: Mapped[str | None] = mapped_column(Text)
    steps_used: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    state: Mapped[dict[str, Any]] = mapped_column(JSONB, nullable=False, default=dict)
    finished_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    planner: Mapped[str] = mapped_column(String(24), nullable=False, default="deterministic")


class AgentStep(UUIDPk, TenantScoped, Timestamped, TenantBase):
    __tablename__ = "agent_steps"

    run_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("bank.agent_runs.id", ondelete="CASCADE"), index=True)
    ordinal: Mapped[int] = mapped_column(Integer, nullable=False)
    node: Mapped[str] = mapped_column(String(64), nullable=False)
    tool: Mapped[str | None] = mapped_column(String(64))
    tool_input: Mapped[dict[str, Any] | None] = mapped_column(JSONB)
    tool_output: Mapped[dict[str, Any] | None] = mapped_column(JSONB)
    status: Mapped[str] = mapped_column(String(16), nullable=False, default="ok")
    duration_ms: Mapped[int | None] = mapped_column(Integer)
    note: Mapped[str | None] = mapped_column(Text)


class AgentApproval(UUIDPk, TenantScoped, Timestamped, TenantBase):
    __tablename__ = "agent_approvals"

    run_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("bank.agent_runs.id", ondelete="CASCADE"), index=True)
    action: Mapped[str] = mapped_column(String(64), nullable=False)
    action_input: Mapped[dict[str, Any]] = mapped_column(JSONB, nullable=False, default=dict)
    status: Mapped[str] = mapped_column(String(16), nullable=False, default="pending")  # pending|approved|rejected
    requested_by: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), nullable=False)
    decided_by: Mapped[uuid.UUID | None] = mapped_column(UUID(as_uuid=True))
    decided_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    comment: Mapped[str | None] = mapped_column(Text)


# ------------------------------------------------------------------------------------------------
# Forecasting, reports, data uploads, incidents, inference monitoring
# ------------------------------------------------------------------------------------------------
class ForecastRun(UUIDPk, TenantScoped, Timestamped, TenantBase):
    __tablename__ = "forecast_runs"

    series_key: Mapped[str] = mapped_column(String(64), nullable=False, index=True)
    created_by: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), nullable=False)
    horizon: Mapped[int] = mapped_column(Integer, nullable=False)
    models: Mapped[list[str]] = mapped_column(JSONB, nullable=False, default=list)
    scenario: Mapped[dict[str, Any]] = mapped_column(JSONB, nullable=False, default=dict)
    result: Mapped[dict[str, Any]] = mapped_column(JSONB, nullable=False, default=dict)
    status: Mapped[str] = mapped_column(String(16), nullable=False, default="completed")
    duration_ms: Mapped[int | None] = mapped_column(Integer)


class Report(UUIDPk, TenantScoped, Timestamped, TenantBase):
    __tablename__ = "reports"

    kind: Mapped[str] = mapped_column(String(32), nullable=False)  # fraud_case|aml_case|credit|model_audit|executive|agent
    title: Mapped[str] = mapped_column(String(300), nullable=False)
    fmt: Mapped[str] = mapped_column(String(8), nullable=False, default="pdf")
    storage_key: Mapped[str] = mapped_column(Text, nullable=False)
    sha256: Mapped[str] = mapped_column(String(64), nullable=False)
    size_bytes: Mapped[int] = mapped_column(BigInteger, nullable=False)
    created_by: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), nullable=False)
    source_type: Mapped[str | None] = mapped_column(String(32))
    source_id: Mapped[str | None] = mapped_column(String(64))
    meta: Mapped[dict[str, Any]] = mapped_column(JSONB, nullable=False, default=dict)


class OrgDataset(UUIDPk, TenantScoped, Timestamped, TenantBase):
    __tablename__ = "org_datasets"

    name: Mapped[str] = mapped_column(String(200), nullable=False)
    fmt: Mapped[str] = mapped_column(String(16), nullable=False)
    storage_key: Mapped[str] = mapped_column(Text, nullable=False)
    sha256: Mapped[str] = mapped_column(String(64), nullable=False)
    rows: Mapped[int] = mapped_column(BigInteger, nullable=False)
    columns: Mapped[int] = mapped_column(Integer, nullable=False)
    schema_json: Mapped[list[dict[str, Any]]] = mapped_column(JSONB, nullable=False, default=list)
    profile: Mapped[dict[str, Any]] = mapped_column(JSONB, nullable=False, default=dict)
    quality: Mapped[list[dict[str, Any]]] = mapped_column(JSONB, nullable=False, default=list)
    quality_status: Mapped[str] = mapped_column(String(16), nullable=False, default="unchecked")
    uploaded_by: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), nullable=False)


class Incident(UUIDPk, TenantScoped, Timestamped, TenantBase):
    __tablename__ = "incidents"

    title: Mapped[str] = mapped_column(String(300), nullable=False)
    category: Mapped[str] = mapped_column(String(32), nullable=False)  # model_drift|model_error|data_quality|pipeline|security|other
    severity: Mapped[str] = mapped_column(String(16), nullable=False)  # low|medium|high|critical
    status: Mapped[str] = mapped_column(String(16), nullable=False, default="open")  # open|mitigating|resolved
    model_name: Mapped[str | None] = mapped_column(String(120))
    description: Mapped[str] = mapped_column(Text, nullable=False, default="")
    opened_by: Mapped[str] = mapped_column(String(320), nullable=False)
    resolved_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    resolution: Mapped[str | None] = mapped_column(Text)
    detected_automatically: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)


class InferenceLog(TenantScoped, Timestamped, TenantBase):
    """Every model inference served to a tenant (for drift monitoring and error tracking)."""

    __tablename__ = "inference_log"
    __table_args__ = (Index("ix_inference_model_ts", "org_id", "model_name", "created_at"),)

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True, autoincrement=True)
    model_name: Mapped[str] = mapped_column(String(120), nullable=False)
    model_version: Mapped[str] = mapped_column(String(64), nullable=False)
    entity_type: Mapped[str] = mapped_column(String(32), nullable=False)
    entity_id: Mapped[str] = mapped_column(String(64), nullable=False)
    score: Mapped[float | None] = mapped_column(Float)
    latency_ms: Mapped[float | None] = mapped_column(Float)
    status: Mapped[str] = mapped_column(String(16), nullable=False, default="ok")  # ok | error
    error: Mapped[str | None] = mapped_column(Text)
    features: Mapped[dict[str, Any]] = mapped_column(JSONB, nullable=False, default=dict)
