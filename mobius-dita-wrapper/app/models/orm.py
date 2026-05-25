"""SQLAlchemy 2.0 ORM models for the DITA service."""
from __future__ import annotations

from datetime import datetime

from sqlalchemy import BigInteger, Boolean, DateTime, Float, Index, Integer, String, Text, UniqueConstraint, func
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column


class Base(DeclarativeBase):
    pass


class TS:
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now(), onupdate=func.now())


class VersionedAsset(Base, TS):
    """Templates and chains share a versioned-asset shape (data/prompt template, chain)."""

    __tablename__ = "versioned_asset"
    asset_id: Mapped[str] = mapped_column(String(64), primary_key=True)
    asset_kind: Mapped[str] = mapped_column(String(32), nullable=False)  # data_template|prompt_template|prompt_chain
    tenant_id: Mapped[str] = mapped_column(String(128), nullable=False)
    name: Mapped[str] = mapped_column(String(256), nullable=False)
    version: Mapped[str] = mapped_column(String(64), nullable=False, default="1")
    status: Mapped[str] = mapped_column(String(32), default="draft")
    spec: Mapped[dict] = mapped_column(JSONB, nullable=False)
    __table_args__ = (Index("ix_asset_tenant_kind_status", "tenant_id", "asset_kind", "status"),)


class AssetVersion(Base):
    """Immutable version history rows for a versioned asset."""

    __tablename__ = "asset_version"
    id: Mapped[int] = mapped_column(BigInteger, primary_key=True, autoincrement=True)
    asset_id: Mapped[str] = mapped_column(String(64), nullable=False)
    asset_kind: Mapped[str] = mapped_column(String(32), nullable=False)
    version: Mapped[str] = mapped_column(String(64), nullable=False)
    spec: Mapped[dict] = mapped_column(JSONB, nullable=False)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
    __table_args__ = (
        UniqueConstraint("asset_id", "version", name="uq_asset_version"),
        Index("ix_asset_version_asset", "asset_id"),
    )


class DocumentJob(Base, TS):
    __tablename__ = "document_job"
    job_id: Mapped[str] = mapped_column(String(64), primary_key=True)
    tenant_id: Mapped[str] = mapped_column(String(128), nullable=False)
    workspace_id: Mapped[str | None] = mapped_column(String(128))
    project_id: Mapped[str | None] = mapped_column(String(128))
    submitted_by: Mapped[str | None] = mapped_column(String(128))
    job_type: Mapped[str] = mapped_column(String(40), nullable=False)
    status: Mapped[str] = mapped_column(String(32), default="queued")
    document_workflow_id: Mapped[str | None] = mapped_column(String(64))
    prompt_chain_run_id: Mapped[str | None] = mapped_column(String(64))
    current_step_id: Mapped[str | None] = mapped_column(String(64))
    progress_percent: Mapped[float | None] = mapped_column(Float)
    request_payload: Mapped[dict] = mapped_column(JSONB, nullable=False)
    failure_reason: Mapped[str | None] = mapped_column(Text)
    cancel_requested: Mapped[bool] = mapped_column(Boolean, default=False)
    pause_requested: Mapped[bool] = mapped_column(Boolean, default=False)
    trace_context: Mapped[dict] = mapped_column(JSONB, nullable=False)
    __table_args__ = (
        Index("ix_docjob_tenant_status", "tenant_id", "status"),
        Index("ix_docjob_tenant_created", "tenant_id", "created_at"),
    )


class PromptChainRun(Base, TS):
    __tablename__ = "prompt_chain_run"
    prompt_chain_run_id: Mapped[str] = mapped_column(String(64), primary_key=True)
    tenant_id: Mapped[str] = mapped_column(String(128), nullable=False)
    prompt_chain_id: Mapped[str] = mapped_column(String(64), nullable=False)
    job_id: Mapped[str | None] = mapped_column(String(64))
    status: Mapped[str] = mapped_column(String(32), default="queued")
    current_step_id: Mapped[str | None] = mapped_column(String(64))
    depth: Mapped[int] = mapped_column(Integer, default=0)  # bounded re-entry guard
    input_data: Mapped[dict] = mapped_column(JSONB, nullable=False)
    step_outputs: Mapped[dict] = mapped_column(JSONB, default=dict)
    cancel_requested: Mapped[bool] = mapped_column(Boolean, default=False)
    failure_reason: Mapped[str | None] = mapped_column(Text)
    __table_args__ = (
        Index("ix_chainrun_tenant_status", "tenant_id", "status"),
        Index("ix_chainrun_chain", "prompt_chain_id"),
    )


class DocumentWorkflow(Base, TS):
    __tablename__ = "document_workflow"
    document_id: Mapped[str] = mapped_column(String(64), primary_key=True)
    tenant_id: Mapped[str] = mapped_column(String(128), nullable=False)
    title: Mapped[str | None] = mapped_column(String(512))
    document_type: Mapped[str | None] = mapped_column(String(64))
    bundle_id: Mapped[str | None] = mapped_column(String(64))
    current_version: Mapped[str] = mapped_column(String(64), default="1")
    workflow_state: Mapped[str] = mapped_column(String(32), default="draft")
    refs: Mapped[dict] = mapped_column(JSONB, default=dict)  # grouped artifact refs
    version: Mapped[int] = mapped_column(Integer, default=1)  # optimistic lock token
    __table_args__ = (
        Index("ix_docwf_tenant_state", "tenant_id", "workflow_state"),
        Index("ix_docwf_tenant_type", "tenant_id", "document_type"),
    )


class DocumentVersion(Base):
    __tablename__ = "document_version"
    id: Mapped[int] = mapped_column(BigInteger, primary_key=True, autoincrement=True)
    document_id: Mapped[str] = mapped_column(String(64), nullable=False)
    version: Mapped[str] = mapped_column(String(64), nullable=False)
    published_artifact_ref: Mapped[str | None] = mapped_column(String(64))
    immutable: Mapped[bool] = mapped_column(Boolean, default=False)
    snapshot: Mapped[dict] = mapped_column(JSONB, nullable=False)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
    __table_args__ = (
        UniqueConstraint("document_id", "version", name="uq_doc_version"),
        Index("ix_docver_doc", "document_id"),
    )


class SeedPacket(Base, TS):
    __tablename__ = "seed_packet"
    seed_packet_id: Mapped[str] = mapped_column(String(64), primary_key=True)
    tenant_id: Mapped[str] = mapped_column(String(128), nullable=False)
    data_template_id: Mapped[str] = mapped_column(String(64), nullable=False)
    status: Mapped[str] = mapped_column(String(32), default="draft")
    document_workflow_id: Mapped[str | None] = mapped_column(String(64))
    prompt_chain_run_id: Mapped[str | None] = mapped_column(String(64))
    reentry_required: Mapped[bool] = mapped_column(Boolean, default=False)
    spec: Mapped[dict] = mapped_column(JSONB, nullable=False)
    version: Mapped[str] = mapped_column(String(64), default="1")
    __table_args__ = (
        Index("ix_seed_tenant_status", "tenant_id", "status"),
        Index("ix_seed_template", "data_template_id"),
        Index("ix_seed_workflow", "document_workflow_id"),
        Index("ix_seed_chainrun", "prompt_chain_run_id"),
    )


class FeedbackRow(Base):
    __tablename__ = "feedback_event"
    feedback_id: Mapped[str] = mapped_column(String(64), primary_key=True)
    tenant_id: Mapped[str] = mapped_column(String(128), nullable=False)
    document_id: Mapped[str] = mapped_column(String(64), nullable=False)
    feedback_type: Mapped[str] = mapped_column(String(40), nullable=False)
    severity: Mapped[str] = mapped_column(String(16), nullable=False)
    comment: Mapped[str] = mapped_column(Text, nullable=False)
    spec: Mapped[dict] = mapped_column(JSONB, nullable=False)
    resolved: Mapped[bool] = mapped_column(Boolean, default=False)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
    __table_args__ = (Index("ix_feedback_tenant_doc", "tenant_id", "document_id", "severity"),)


class ArtifactRef(Base):
    __tablename__ = "artifact_ref"
    artifact_ref_id: Mapped[str] = mapped_column(String(64), primary_key=True)
    artifact_type: Mapped[str] = mapped_column(String(64), nullable=False)
    storage_system: Mapped[str] = mapped_column(String(32), default="DITA")
    version: Mapped[str] = mapped_column(String(64), default="1")
    tenant_id: Mapped[str] = mapped_column(String(128), nullable=False)
    job_id: Mapped[str | None] = mapped_column(String(64))
    prompt_chain_run_id: Mapped[str | None] = mapped_column(String(64))
    document_id: Mapped[str | None] = mapped_column(String(64))
    uri: Mapped[str | None] = mapped_column(Text)
    checksum: Mapped[str | None] = mapped_column(String(128))
    data_classification: Mapped[str | None] = mapped_column(String(64))
    immutable: Mapped[bool] = mapped_column(Boolean, default=False)
    content: Mapped[dict | None] = mapped_column(JSONB)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
    __table_args__ = (
        Index("ix_dita_artifact_tenant_type", "tenant_id", "artifact_type"),
        Index("ix_dita_artifact_job", "job_id"),
        Index("ix_dita_artifact_doc", "document_id"),
    )


class LineageEdge(Base):
    __tablename__ = "lineage_edge"
    lineage_edge_id: Mapped[str] = mapped_column(String(64), primary_key=True)
    tenant_id: Mapped[str] = mapped_column(String(128), nullable=False)
    from_artifact_ref: Mapped[str] = mapped_column(String(64), nullable=False)
    to_artifact_ref: Mapped[str] = mapped_column(String(64), nullable=False)
    edge_type: Mapped[str] = mapped_column(String(32), nullable=False)
    job_id: Mapped[str | None] = mapped_column(String(64))
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
    __table_args__ = (
        Index("ix_dita_lineage_from", "from_artifact_ref"),
        Index("ix_dita_lineage_to", "to_artifact_ref"),
    )


class ExecutionEvent(Base):
    __tablename__ = "execution_event"
    event_id: Mapped[str] = mapped_column(String(64), primary_key=True)
    event_sequence: Mapped[int] = mapped_column(BigInteger, nullable=False)
    tenant_id: Mapped[str] = mapped_column(String(128), nullable=False)
    job_id: Mapped[str] = mapped_column(String(64), nullable=False)
    event_type: Mapped[str] = mapped_column(String(64), nullable=False)
    status: Mapped[str | None] = mapped_column(String(32))
    progress_percent: Mapped[float | None] = mapped_column(Float)
    message: Mapped[str | None] = mapped_column(Text)
    artifact_refs: Mapped[list | None] = mapped_column(JSONB)
    occurred_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
    trace_id: Mapped[str] = mapped_column(String(128), nullable=False)
    __table_args__ = (
        UniqueConstraint("job_id", "event_sequence", name="uq_dita_event_job_seq"),
        Index("ix_dita_event_job", "job_id", "event_sequence"),
    )


class IdempotencyKey(Base):
    __tablename__ = "idempotency_key"
    id: Mapped[int] = mapped_column(BigInteger, primary_key=True, autoincrement=True)
    tenant_id: Mapped[str] = mapped_column(String(128), nullable=False)
    endpoint: Mapped[str] = mapped_column(String(64), nullable=False)
    idem_key: Mapped[str] = mapped_column(String(256), nullable=False)
    resource_id: Mapped[str] = mapped_column(String(64), nullable=False)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
    __table_args__ = (UniqueConstraint("tenant_id", "endpoint", "idem_key", name="uq_dita_idem"),)
