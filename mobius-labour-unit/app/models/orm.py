"""SQLAlchemy 2.0 ORM models.

These are the production tables behind the labor gateway. They map to the PI runtime
schemas (labor_call, labor_batch, execution_event, artifact_ref, cost_record, etc.)
but here they are owned locally by the gateway service. Raw output is immutable once
written (enforced in the repository layer and by a DB trigger created in the migration).
"""
from __future__ import annotations

from datetime import datetime

from sqlalchemy import (
    BigInteger,
    Boolean,
    DateTime,
    Float,
    ForeignKey,
    Index,
    Integer,
    String,
    Text,
    UniqueConstraint,
    func,
)
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column, relationship


class Base(DeclarativeBase):
    pass


class TimestampMixin:
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), onupdate=func.now()
    )


class LaborJob(Base, TimestampMixin):
    __tablename__ = "labor_job"

    job_id: Mapped[str] = mapped_column(String(64), primary_key=True)
    tenant_id: Mapped[str] = mapped_column(String(128), nullable=False)
    workspace_id: Mapped[str | None] = mapped_column(String(128))
    project_id: Mapped[str | None] = mapped_column(String(128))
    cost_center_id: Mapped[str | None] = mapped_column(String(128))
    labor_call_id: Mapped[str | None] = mapped_column(String(64))
    batch_id: Mapped[str | None] = mapped_column(String(64), ForeignKey("labor_batch.batch_id"))
    status: Mapped[str] = mapped_column(String(32), nullable=False, default="queued")
    stage: Mapped[str | None] = mapped_column(String(256))  # OPAQUE — never branched on
    executor_used: Mapped[str | None] = mapped_column(String(128))
    progress_percent: Mapped[float | None] = mapped_column(Float)
    repair_attempt_count: Mapped[int] = mapped_column(Integer, default=0)
    validation_status: Mapped[str | None] = mapped_column(String(32))
    request_payload: Mapped[dict] = mapped_column(JSONB, nullable=False)
    failure_reason: Mapped[str | None] = mapped_column(Text)
    started_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    completed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    cancelled_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    cancel_requested: Mapped[bool] = mapped_column(Boolean, default=False)
    pause_requested: Mapped[bool] = mapped_column(Boolean, default=False)
    trace_context: Mapped[dict] = mapped_column(JSONB, nullable=False)

    artifacts: Mapped[list["ArtifactRef"]] = relationship(
        back_populates="job", cascade="all, delete-orphan"
    )

    __table_args__ = (
        Index("ix_labor_job_tenant_status", "tenant_id", "status"),
        Index("ix_labor_job_tenant_created", "tenant_id", "created_at"),
        Index("ix_labor_job_batch", "batch_id"),
        Index("ix_labor_job_stage", "tenant_id", "stage"),
    )


class LaborBatch(Base, TimestampMixin):
    __tablename__ = "labor_batch"

    batch_id: Mapped[str] = mapped_column(String(64), primary_key=True)
    tenant_id: Mapped[str] = mapped_column(String(128), nullable=False)
    status: Mapped[str] = mapped_column(String(32), nullable=False, default="queued")
    submitted_count: Mapped[int] = mapped_column(Integer, default=0)
    failure_policy: Mapped[str] = mapped_column(String(32), default="continue_on_error")
    concurrency_limit: Mapped[int | None] = mapped_column(Integer)
    executor_policy_id: Mapped[str | None] = mapped_column(String(64))
    idempotency_key: Mapped[str] = mapped_column(String(256), nullable=False)
    cancel_requested: Mapped[bool] = mapped_column(Boolean, default=False)

    __table_args__ = (
        UniqueConstraint("tenant_id", "idempotency_key", name="uq_batch_tenant_idem"),
        Index("ix_labor_batch_tenant_status", "tenant_id", "status"),
        Index("ix_labor_batch_tenant_created", "tenant_id", "created_at"),
    )


class BatchItem(Base, TimestampMixin):
    __tablename__ = "batch_item"

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True, autoincrement=True)
    batch_id: Mapped[str] = mapped_column(String(64), ForeignKey("labor_batch.batch_id"), nullable=False)
    custom_id: Mapped[str] = mapped_column(String(256), nullable=False)  # echoed verbatim
    labor_call_id: Mapped[str | None] = mapped_column(String(64))
    job_id: Mapped[str | None] = mapped_column(String(64))
    status: Mapped[str] = mapped_column(String(32), default="queued")

    __table_args__ = (
        UniqueConstraint("batch_id", "custom_id", name="uq_batch_item_custom"),
        Index("ix_batch_item_batch_status", "batch_id", "status"),
    )


class ArtifactRef(Base):
    __tablename__ = "artifact_ref"

    artifact_ref_id: Mapped[str] = mapped_column(String(64), primary_key=True)
    artifact_type: Mapped[str] = mapped_column(String(64), nullable=False)
    storage_system: Mapped[str] = mapped_column(String(32), nullable=False, default="GATEWAY")
    version: Mapped[str] = mapped_column(String(64), nullable=False, default="1")
    tenant_id: Mapped[str] = mapped_column(String(128), nullable=False)
    job_id: Mapped[str | None] = mapped_column(String(64), ForeignKey("labor_job.job_id"))
    labor_call_id: Mapped[str | None] = mapped_column(String(64))
    uri: Mapped[str | None] = mapped_column(Text)
    checksum: Mapped[str | None] = mapped_column(String(128))
    data_classification: Mapped[str | None] = mapped_column(String(64))
    immutable: Mapped[bool] = mapped_column(Boolean, default=False)
    # inline content store for small payloads; large ones go to object store via uri
    content: Mapped[dict | None] = mapped_column(JSONB)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())

    job: Mapped["LaborJob"] = relationship(back_populates="artifacts")

    __table_args__ = (
        Index("ix_artifact_tenant_type", "tenant_id", "artifact_type"),
        Index("ix_artifact_job", "job_id"),
        Index("ix_artifact_labor_call", "labor_call_id"),
    )


class ExecutionEvent(Base):
    """Append-only. No update/delete in the repo layer; enforced by grants in prod."""

    __tablename__ = "execution_event"

    event_id: Mapped[str] = mapped_column(String(64), primary_key=True)
    event_sequence: Mapped[int] = mapped_column(BigInteger, nullable=False)
    tenant_id: Mapped[str] = mapped_column(String(128), nullable=False)
    job_id: Mapped[str] = mapped_column(String(64), nullable=False)
    labor_call_id: Mapped[str | None] = mapped_column(String(64))
    batch_id: Mapped[str | None] = mapped_column(String(64))
    event_type: Mapped[str] = mapped_column(String(64), nullable=False)
    status: Mapped[str | None] = mapped_column(String(32))
    progress_percent: Mapped[float | None] = mapped_column(Float)
    message: Mapped[str | None] = mapped_column(Text)
    artifact_refs: Mapped[list | None] = mapped_column(JSONB)
    correlation_id: Mapped[str | None] = mapped_column(String(64))
    causation_id: Mapped[str | None] = mapped_column(String(64))
    parent_event_id: Mapped[str | None] = mapped_column(String(64))
    replayable: Mapped[bool] = mapped_column(Boolean, default=True)
    payload: Mapped[dict | None] = mapped_column(JSONB)
    occurred_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
    trace_id: Mapped[str] = mapped_column(String(128), nullable=False)

    __table_args__ = (
        UniqueConstraint("job_id", "event_sequence", name="uq_event_job_seq"),
        Index("ix_event_tenant_job", "tenant_id", "job_id"),
        Index("ix_event_job_seq", "job_id", "event_sequence"),
        Index("ix_event_occurred", "occurred_at"),
    )


class CostRecord(Base):
    __tablename__ = "cost_record"

    cost_record_id: Mapped[str] = mapped_column(String(64), primary_key=True)
    tenant_id: Mapped[str] = mapped_column(String(128), nullable=False)
    cost_center_id: Mapped[str | None] = mapped_column(String(128))
    labor_call_id: Mapped[str | None] = mapped_column(String(64))
    batch_id: Mapped[str | None] = mapped_column(String(64))
    amount: Mapped[float] = mapped_column(Float, default=0.0)
    currency: Mapped[str] = mapped_column(String(8), default="USD")
    rate_class: Mapped[str | None] = mapped_column(String(16))
    cache_read_tokens: Mapped[int] = mapped_column(Integer, default=0)
    cache_write_tokens: Mapped[int] = mapped_column(Integer, default=0)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())

    __table_args__ = (
        Index("ix_cost_tenant_center_created", "tenant_id", "cost_center_id", "created_at"),
    )


class IdempotencyKey(Base):
    """Maps (tenant, key) -> the original job/batch id so replays return the original."""

    __tablename__ = "idempotency_key"

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True, autoincrement=True)
    tenant_id: Mapped[str] = mapped_column(String(128), nullable=False)
    endpoint: Mapped[str] = mapped_column(String(64), nullable=False)
    idem_key: Mapped[str] = mapped_column(String(256), nullable=False)
    resource_id: Mapped[str] = mapped_column(String(64), nullable=False)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())

    __table_args__ = (
        UniqueConstraint("tenant_id", "endpoint", "idem_key", name="uq_idem"),
    )


class ExecutorPolicy(Base, TimestampMixin):
    __tablename__ = "executor_policy"

    executor_policy_id: Mapped[str] = mapped_column(String(64), primary_key=True)
    tenant_id: Mapped[str] = mapped_column(String(128), nullable=False)
    name: Mapped[str] = mapped_column(String(256), nullable=False)
    spec: Mapped[dict] = mapped_column(JSONB, nullable=False)

    __table_args__ = (Index("ix_executor_policy_tenant", "tenant_id"),)


class ModelPolicy(Base, TimestampMixin):
    __tablename__ = "model_policy"

    model_policy_id: Mapped[str] = mapped_column(String(64), primary_key=True)
    tenant_id: Mapped[str] = mapped_column(String(128), nullable=False)
    name: Mapped[str | None] = mapped_column(String(256))
    spec: Mapped[dict] = mapped_column(JSONB, nullable=False)

    __table_args__ = (Index("ix_model_policy_tenant", "tenant_id"),)
