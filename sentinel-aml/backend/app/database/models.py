"""Relational schema for the complete AML investigation workflow.

Every business table carries tenant_id; every query in the service layer filters on it.
"""
from __future__ import annotations

import enum
import uuid
from datetime import UTC, datetime

from sqlalchemy import (
    BigInteger,
    Boolean,
    CheckConstraint,
    DateTime,
    Enum,
    Float,
    ForeignKey,
    Index,
    Integer,
    Numeric,
    String,
    Text,
    UniqueConstraint,
)
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column, relationship

from app.core.config import get_settings
from app.database.types import EmbeddingVector, JsonType


def utcnow() -> datetime:
    return datetime.now(UTC)


def new_id() -> str:
    return str(uuid.uuid4())


class Base(DeclarativeBase):
    pass


class TimestampMixin:
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow, nullable=False)
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow, onupdate=utcnow, nullable=False)


# ----------------------------------------------------------------------------- enums
class Role(str, enum.Enum):
    analyst = "analyst"            # L1: triage, start investigations
    investigator = "investigator"  # L2: approve closures / monitoring
    mlro = "mlro"                  # Money Laundering Reporting Officer: STR + restrictions
    auditor = "auditor"            # read-only, audit log
    admin = "admin"                # platform admin, ingestion, KB management


class AlertStatus(str, enum.Enum):
    NEW = "NEW"
    INVESTIGATING = "INVESTIGATING"
    PENDING_APPROVAL = "PENDING_APPROVAL"
    NEEDS_MANUAL_REVIEW = "NEEDS_MANUAL_REVIEW"
    CLOSED_FALSE_POSITIVE = "CLOSED_FALSE_POSITIVE"
    MONITORING = "MONITORING"
    ESCALATED_STR = "ESCALATED_STR"
    ACCOUNT_RESTRICTED = "ACCOUNT_RESTRICTED"


class Priority(str, enum.Enum):
    LOW = "LOW"
    MEDIUM = "MEDIUM"
    HIGH = "HIGH"
    CRITICAL = "CRITICAL"


class WorkflowStatus(str, enum.Enum):
    QUEUED = "QUEUED"
    RUNNING = "RUNNING"
    AWAITING_APPROVAL = "AWAITING_APPROVAL"
    COMPLETED = "COMPLETED"
    NEEDS_MANUAL_REVIEW = "NEEDS_MANUAL_REVIEW"
    FAILED = "FAILED"


class RecommendedAction(str, enum.Enum):
    CLOSE_FALSE_POSITIVE = "CLOSE_FALSE_POSITIVE"
    ENHANCED_MONITORING = "ENHANCED_MONITORING"
    ESCALATE_STR = "ESCALATE_STR"
    RESTRICT_ACCOUNT = "RESTRICT_ACCOUNT"


class RecommendationStatus(str, enum.Enum):
    PENDING_APPROVAL = "PENDING_APPROVAL"
    APPROVED = "APPROVED"
    REJECTED = "REJECTED"
    AUTO_APPLIED = "AUTO_APPLIED"
    EXECUTED = "EXECUTED"


class DocStatus(str, enum.Enum):
    PENDING_REVIEW = "PENDING_REVIEW"
    APPROVED = "APPROVED"
    QUARANTINED = "QUARANTINED"


def _enum(e):
    return Enum(e, native_enum=False, length=40, validate_strings=True)


# ----------------------------------------------------------------------------- identity
class Tenant(Base, TimestampMixin):
    __tablename__ = "tenants"
    id: Mapped[str] = mapped_column(String(64), primary_key=True)
    name: Mapped[str] = mapped_column(String(200), nullable=False)
    is_active: Mapped[bool] = mapped_column(Boolean, default=True, nullable=False)


class User(Base, TimestampMixin):
    __tablename__ = "users"
    __table_args__ = (UniqueConstraint("tenant_id", "username", name="uq_user_tenant_username"),)
    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=new_id)
    tenant_id: Mapped[str] = mapped_column(ForeignKey("tenants.id"), nullable=False, index=True)
    username: Mapped[str] = mapped_column(String(100), nullable=False)
    password_hash: Mapped[str] = mapped_column(String(300), nullable=False)
    role: Mapped[Role] = mapped_column(_enum(Role), nullable=False)
    is_active: Mapped[bool] = mapped_column(Boolean, default=True, nullable=False)
    failed_logins: Mapped[int] = mapped_column(Integer, default=0, nullable=False)


# ----------------------------------------------------------------------------- core banking data
class Customer(Base, TimestampMixin):
    __tablename__ = "customers"
    id: Mapped[str] = mapped_column(String(64), primary_key=True)
    tenant_id: Mapped[str] = mapped_column(ForeignKey("tenants.id"), nullable=False, index=True)
    full_name: Mapped[str] = mapped_column(String(200), nullable=False)  # PII
    segment: Mapped[str] = mapped_column(String(40), nullable=False)
    country: Mapped[str] = mapped_column(String(2), nullable=False)
    kyc_risk_rating: Mapped[str] = mapped_column(String(10), nullable=False)
    pep_flag: Mapped[bool] = mapped_column(Boolean, default=False, nullable=False)
    occupation: Mapped[str] = mapped_column(String(100), nullable=False)
    expected_monthly_turnover_eur: Mapped[float] = mapped_column(Numeric(14, 2, asdecimal=False), nullable=False)
    onboarded_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    monitoring_level: Mapped[str] = mapped_column(String(20), default="standard", nullable=False)
    __table_args__ = (
        CheckConstraint("kyc_risk_rating in ('low','medium','high')", name="ck_customer_kyc"),
        CheckConstraint("expected_monthly_turnover_eur >= 0", name="ck_customer_turnover"),
    )


class Account(Base, TimestampMixin):
    __tablename__ = "accounts"
    id: Mapped[str] = mapped_column(String(64), primary_key=True)
    tenant_id: Mapped[str] = mapped_column(ForeignKey("tenants.id"), nullable=False, index=True)
    customer_id: Mapped[str] = mapped_column(ForeignKey("customers.id"), nullable=False, index=True)
    iban: Mapped[str] = mapped_column(String(34), nullable=False)  # PII
    account_type: Mapped[str] = mapped_column(String(20), nullable=False)
    currency: Mapped[str] = mapped_column(String(3), default="EUR", nullable=False)
    status: Mapped[str] = mapped_column(String(20), default="active", nullable=False)
    opened_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    __table_args__ = (CheckConstraint("status in ('active','restricted','closed')", name="ck_account_status"),)


class IngestionBatch(Base):
    """Data lineage + versioning: one row per ingested file."""
    __tablename__ = "ingestion_batches"
    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=new_id)
    entity: Mapped[str] = mapped_column(String(40), nullable=False)
    source_name: Mapped[str] = mapped_column(String(300), nullable=False)
    file_sha256: Mapped[str] = mapped_column(String(64), nullable=False, index=True)
    status: Mapped[str] = mapped_column(String(30), nullable=False)
    rows_received: Mapped[int] = mapped_column(Integer, default=0)
    rows_loaded: Mapped[int] = mapped_column(Integer, default=0)
    rows_rejected: Mapped[int] = mapped_column(Integer, default=0)
    rows_duplicate: Mapped[int] = mapped_column(Integer, default=0)
    quality_report: Mapped[dict] = mapped_column(JsonType, default=dict)
    triggered_by: Mapped[str] = mapped_column(String(100), default="system")
    started_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)
    finished_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))


class RejectedRecord(Base):
    __tablename__ = "rejected_records"
    id: Mapped[int] = mapped_column(BigInteger().with_variant(Integer, "sqlite"), primary_key=True, autoincrement=True)
    batch_id: Mapped[str] = mapped_column(ForeignKey("ingestion_batches.id"), nullable=False, index=True)
    reason: Mapped[str] = mapped_column(String(200), nullable=False)
    raw: Mapped[dict] = mapped_column(JsonType, nullable=False)


class Transaction(Base):
    __tablename__ = "transactions"
    id: Mapped[str] = mapped_column(String(64), primary_key=True)  # source system id = idempotency key
    tenant_id: Mapped[str] = mapped_column(ForeignKey("tenants.id"), nullable=False)
    account_id: Mapped[str] = mapped_column(ForeignKey("accounts.id"), nullable=False)
    customer_id: Mapped[str] = mapped_column(ForeignKey("customers.id"), nullable=False)
    ts: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    amount_eur: Mapped[float] = mapped_column(Numeric(14, 2, asdecimal=False), nullable=False)
    original_amount: Mapped[float] = mapped_column(Numeric(14, 2, asdecimal=False), nullable=False)
    original_currency: Mapped[str] = mapped_column(String(3), nullable=False)
    direction: Mapped[str] = mapped_column(String(3), nullable=False)
    channel: Mapped[str] = mapped_column(String(30), nullable=False)
    counterparty_id: Mapped[str] = mapped_column(String(64), nullable=False)
    counterparty_country: Mapped[str] = mapped_column(String(2), nullable=False)
    reference_text: Mapped[str] = mapped_column(String(140), default="", nullable=False)  # untrusted free text
    reference_flags: Mapped[list] = mapped_column(JsonType, default=list, nullable=False)  # injection scan at ingestion
    batch_id: Mapped[str | None] = mapped_column(ForeignKey("ingestion_batches.id"))
    __table_args__ = (
        CheckConstraint("amount_eur > 0", name="ck_txn_amount_positive"),
        CheckConstraint("direction in ('in','out')", name="ck_txn_direction"),
        Index("ix_txn_tenant_customer_ts", "tenant_id", "customer_id", "ts"),
        Index("ix_txn_tenant_ts", "tenant_id", "ts"),
    )


# ----------------------------------------------------------------------------- ML
class ModelVersion(Base):
    __tablename__ = "model_versions"
    __table_args__ = (UniqueConstraint("name", "version", name="uq_model_name_version"),)
    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=new_id)
    name: Mapped[str] = mapped_column(String(100), nullable=False)
    version: Mapped[str] = mapped_column(String(60), nullable=False)
    algorithm: Mapped[str] = mapped_column(String(100), nullable=False)
    stage: Mapped[str] = mapped_column(String(20), default="staging", nullable=False)  # staging|production|archived
    artifact_uri: Mapped[str] = mapped_column(String(500), nullable=False)
    artifact_sha256: Mapped[str] = mapped_column(String(64), nullable=False)
    training_data_hash: Mapped[str] = mapped_column(String(64), nullable=False)
    feature_names: Mapped[list] = mapped_column(JsonType, nullable=False)
    params: Mapped[dict] = mapped_column(JsonType, default=dict)
    metrics: Mapped[dict] = mapped_column(JsonType, default=dict)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)


class Alert(Base, TimestampMixin):
    __tablename__ = "alerts"
    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=new_id)
    tenant_id: Mapped[str] = mapped_column(ForeignKey("tenants.id"), nullable=False)
    customer_id: Mapped[str] = mapped_column(ForeignKey("customers.id"), nullable=False)
    rule_code: Mapped[str] = mapped_column(String(40), nullable=False)
    dedupe_key: Mapped[str] = mapped_column(String(200), nullable=False, unique=True)
    triggered_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    window_start: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    window_end: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    rule_details: Mapped[dict] = mapped_column(JsonType, default=dict)
    features: Mapped[dict] = mapped_column(JsonType, default=dict)  # point-in-time feature snapshot
    feature_set_version: Mapped[str] = mapped_column(String(20), nullable=False)
    status: Mapped[AlertStatus] = mapped_column(_enum(AlertStatus), default=AlertStatus.NEW, nullable=False)
    priority: Mapped[Priority] = mapped_column(_enum(Priority), default=Priority.MEDIUM, nullable=False)
    risk_score: Mapped[float | None] = mapped_column(Float)
    anomaly_score: Mapped[float | None] = mapped_column(Float)
    model_version_id: Mapped[str | None] = mapped_column(ForeignKey("model_versions.id"))
    lock_version: Mapped[int] = mapped_column(Integer, default=1, nullable=False)
    __mapper_args__ = {"version_id_col": lock_version}  # optimistic concurrency
    __table_args__ = (
        Index("ix_alert_tenant_status_score", "tenant_id", "status", "risk_score"),
        Index("ix_alert_tenant_customer", "tenant_id", "customer_id"),
        CheckConstraint("risk_score IS NULL OR (risk_score >= 0 AND risk_score <= 1)", name="ck_alert_score"),
    )


class Prediction(Base):
    __tablename__ = "predictions"
    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=new_id)
    tenant_id: Mapped[str] = mapped_column(ForeignKey("tenants.id"), nullable=False)
    alert_id: Mapped[str] = mapped_column(ForeignKey("alerts.id"), nullable=False, index=True)
    model_version_id: Mapped[str] = mapped_column(ForeignKey("model_versions.id"), nullable=False, index=True)
    score: Mapped[float] = mapped_column(Float, nullable=False)
    raw_score: Mapped[float] = mapped_column(Float, nullable=False)
    anomaly_score: Mapped[float] = mapped_column(Float, nullable=False)
    above_threshold: Mapped[bool] = mapped_column(Boolean, nullable=False)
    top_contributors: Mapped[list] = mapped_column(JsonType, default=list)
    latency_ms: Mapped[float] = mapped_column(Float, default=0.0)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow, index=True)


# ----------------------------------------------------------------------------- RAG
class KBDocument(Base, TimestampMixin):
    __tablename__ = "kb_documents"
    id: Mapped[str] = mapped_column(String(100), primary_key=True)
    tenant_id: Mapped[str | None] = mapped_column(ForeignKey("tenants.id"), index=True)  # NULL = global
    title: Mapped[str] = mapped_column(String(300), nullable=False)
    doc_type: Mapped[str] = mapped_column(String(40), nullable=False)
    source: Mapped[str] = mapped_column(String(300), nullable=False)
    classification: Mapped[str] = mapped_column(String(20), nullable=False)
    clearance_level: Mapped[int] = mapped_column(Integer, nullable=False)
    version: Mapped[str] = mapped_column(String(20), default="1.0")
    content_sha256: Mapped[str] = mapped_column(String(64), nullable=False)
    status: Mapped[DocStatus] = mapped_column(_enum(DocStatus), nullable=False)
    injection_findings: Mapped[list] = mapped_column(JsonType, default=list)
    uploaded_by: Mapped[str] = mapped_column(String(100), nullable=False)
    approved_by: Mapped[str | None] = mapped_column(String(100))
    chunks: Mapped[list[KBChunk]] = relationship(back_populates="document", cascade="all, delete-orphan")


class KBChunk(Base):
    __tablename__ = "kb_chunks"
    id: Mapped[str] = mapped_column(String(140), primary_key=True)
    document_id: Mapped[str] = mapped_column(ForeignKey("kb_documents.id", ondelete="CASCADE"), nullable=False, index=True)
    tenant_id: Mapped[str | None] = mapped_column(String(64), index=True)
    clearance_level: Mapped[int] = mapped_column(Integer, nullable=False)
    is_active: Mapped[bool] = mapped_column(Boolean, default=False, nullable=False)  # only approved docs are active
    chunk_index: Mapped[int] = mapped_column(Integer, nullable=False)
    section: Mapped[str] = mapped_column(String(300), nullable=False)
    text: Mapped[str] = mapped_column(Text, nullable=False)
    token_estimate: Mapped[int] = mapped_column(Integer, nullable=False)
    embedding = mapped_column(EmbeddingVector(get_settings().embedding_dim), nullable=True)
    embedding_model: Mapped[str] = mapped_column(String(100), nullable=False)
    document: Mapped[KBDocument] = relationship(back_populates="chunks")
    __table_args__ = (Index("ix_chunk_acl", "is_active", "tenant_id", "clearance_level"),)


# ----------------------------------------------------------------------------- agentic workflow
class WorkflowRun(Base, TimestampMixin):
    __tablename__ = "workflow_runs"
    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=new_id)
    tenant_id: Mapped[str] = mapped_column(ForeignKey("tenants.id"), nullable=False)
    alert_id: Mapped[str] = mapped_column(ForeignKey("alerts.id"), nullable=False, index=True)
    status: Mapped[WorkflowStatus] = mapped_column(_enum(WorkflowStatus), nullable=False)
    idempotency_key: Mapped[str | None] = mapped_column(String(100), unique=True)
    started_by: Mapped[str] = mapped_column(String(100), nullable=False)
    started_by_role: Mapped[str] = mapped_column(String(20), default="analyst", nullable=False)
    llm_provider: Mapped[str] = mapped_column(String(40), nullable=False)
    failure_reason: Mapped[str | None] = mapped_column(Text)
    context_flags: Mapped[list] = mapped_column(JsonType, default=list)
    total_tokens: Mapped[int] = mapped_column(Integer, default=0)
    total_cost_usd: Mapped[float] = mapped_column(Float, default=0.0)
    latency_ms: Mapped[float | None] = mapped_column(Float)
    finished_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    __table_args__ = (Index("ix_workflow_tenant_status", "tenant_id", "status"),)


class AgentStep(Base):
    __tablename__ = "agent_steps"
    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=new_id)
    workflow_id: Mapped[str] = mapped_column(ForeignKey("workflow_runs.id", ondelete="CASCADE"), nullable=False, index=True)
    step_index: Mapped[int] = mapped_column(Integer, nullable=False)
    agent_name: Mapped[str] = mapped_column(String(60), nullable=False)
    status: Mapped[str] = mapped_column(String(20), nullable=False)  # ok|degraded|failed
    output: Mapped[dict] = mapped_column(JsonType, default=dict)
    error: Mapped[str | None] = mapped_column(Text)
    latency_ms: Mapped[float] = mapped_column(Float, default=0.0)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)


class ToolInvocation(Base):
    __tablename__ = "tool_invocations"
    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=new_id)
    workflow_id: Mapped[str] = mapped_column(ForeignKey("workflow_runs.id", ondelete="CASCADE"), nullable=False, index=True)
    agent_name: Mapped[str] = mapped_column(String(60), nullable=False)
    tool_name: Mapped[str] = mapped_column(String(60), nullable=False)
    arguments: Mapped[dict] = mapped_column(JsonType, default=dict)
    permitted: Mapped[bool] = mapped_column(Boolean, nullable=False)
    status: Mapped[str] = mapped_column(String(20), nullable=False)
    result_ref_ids: Mapped[list] = mapped_column(JsonType, default=list)
    latency_ms: Mapped[float] = mapped_column(Float, default=0.0)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)


class LLMCall(Base):
    """LLMOps ledger: which model + prompt version produced which output, at what cost."""
    __tablename__ = "llm_calls"
    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=new_id)
    tenant_id: Mapped[str | None] = mapped_column(String(64), index=True)
    workflow_id: Mapped[str | None] = mapped_column(String(36), index=True)
    agent_name: Mapped[str] = mapped_column(String(60), nullable=False)
    provider: Mapped[str] = mapped_column(String(40), nullable=False)
    model: Mapped[str] = mapped_column(String(100), nullable=False)
    prompt_name: Mapped[str] = mapped_column(String(100), nullable=False)
    prompt_version: Mapped[str] = mapped_column(String(20), nullable=False)
    prompt_sha256: Mapped[str] = mapped_column(String(64), nullable=False)
    input_tokens: Mapped[int] = mapped_column(Integer, default=0)
    output_tokens: Mapped[int] = mapped_column(Integer, default=0)
    tokens_estimated: Mapped[bool] = mapped_column(Boolean, default=False)
    cost_usd: Mapped[float] = mapped_column(Float, default=0.0)
    latency_ms: Mapped[float] = mapped_column(Float, default=0.0)
    attempts: Mapped[int] = mapped_column(Integer, default=1)
    status: Mapped[str] = mapped_column(String(30), nullable=False)  # ok|schema_invalid|timeout|error|circuit_open
    schema_valid: Mapped[bool] = mapped_column(Boolean, default=False)
    retrieved_chunk_ids: Mapped[list] = mapped_column(JsonType, default=list)
    output_preview: Mapped[str | None] = mapped_column(Text)
    error: Mapped[str | None] = mapped_column(Text)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow, index=True)


class Recommendation(Base, TimestampMixin):
    __tablename__ = "recommendations"
    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=new_id)
    tenant_id: Mapped[str] = mapped_column(ForeignKey("tenants.id"), nullable=False)
    workflow_id: Mapped[str] = mapped_column(ForeignKey("workflow_runs.id"), nullable=False, unique=True)
    alert_id: Mapped[str] = mapped_column(ForeignKey("alerts.id"), nullable=False, index=True)
    action: Mapped[RecommendedAction] = mapped_column(_enum(RecommendedAction), nullable=False)
    confidence: Mapped[float] = mapped_column(Float, nullable=False)
    narrative: Mapped[str] = mapped_column(Text, nullable=False)
    key_findings: Mapped[list] = mapped_column(JsonType, default=list)
    citations: Mapped[list] = mapped_column(JsonType, default=list)
    verification: Mapped[dict] = mapped_column(JsonType, default=dict)
    requires_approval: Mapped[bool] = mapped_column(Boolean, nullable=False)
    required_role: Mapped[str | None] = mapped_column(String(20))
    policy_reasons: Mapped[list] = mapped_column(JsonType, default=list)
    status: Mapped[RecommendationStatus] = mapped_column(_enum(RecommendationStatus), nullable=False)
    __table_args__ = (
        CheckConstraint("confidence >= 0 AND confidence <= 1", name="ck_rec_confidence"),
        Index("ix_rec_tenant_status", "tenant_id", "status"),
    )


class Approval(Base):
    __tablename__ = "approvals"
    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=new_id)
    tenant_id: Mapped[str] = mapped_column(ForeignKey("tenants.id"), nullable=False)
    recommendation_id: Mapped[str] = mapped_column(ForeignKey("recommendations.id"), nullable=False, unique=True)
    decided_by: Mapped[str] = mapped_column(String(100), nullable=False)
    decided_by_role: Mapped[str] = mapped_column(String(20), nullable=False)
    decision: Mapped[str] = mapped_column(String(10), nullable=False)
    comment: Mapped[str] = mapped_column(Text, nullable=False)
    decided_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)
    __table_args__ = (CheckConstraint("decision in ('approve','reject')", name="ck_approval_decision"),)


class STRReport(Base, TimestampMixin):
    """Suspicious Transaction Report draft ready for submission to FIU Ireland (goAML) + Revenue."""
    __tablename__ = "str_reports"
    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=new_id)
    tenant_id: Mapped[str] = mapped_column(ForeignKey("tenants.id"), nullable=False)
    alert_id: Mapped[str] = mapped_column(ForeignKey("alerts.id"), nullable=False)
    recommendation_id: Mapped[str] = mapped_column(ForeignKey("recommendations.id"), nullable=False, unique=True)
    narrative: Mapped[str] = mapped_column(Text, nullable=False)
    status: Mapped[str] = mapped_column(String(30), default="READY_FOR_SUBMISSION", nullable=False)
    approved_by: Mapped[str] = mapped_column(String(100), nullable=False)


class AuditLog(Base):
    """Append-only, hash-chained audit trail (tamper evident)."""
    __tablename__ = "audit_log"
    id: Mapped[int] = mapped_column(BigInteger().with_variant(Integer, "sqlite"), primary_key=True, autoincrement=True)
    tenant_id: Mapped[str] = mapped_column(String(64), nullable=False, index=True)
    actor: Mapped[str] = mapped_column(String(100), nullable=False)
    action: Mapped[str] = mapped_column(String(80), nullable=False)
    entity_type: Mapped[str] = mapped_column(String(60), nullable=False)
    entity_id: Mapped[str] = mapped_column(String(100), nullable=False)
    details: Mapped[dict] = mapped_column(JsonType, default=dict)
    request_id: Mapped[str] = mapped_column(String(64), default="-")
    prev_hash: Mapped[str] = mapped_column(String(64), nullable=False)
    hash: Mapped[str] = mapped_column(String(64), nullable=False)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow, index=True)
