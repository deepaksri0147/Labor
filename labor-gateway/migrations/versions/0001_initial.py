"""initial labor gateway schema

Revision ID: 0001_initial
Revises:
Create Date: 2026-05-21
"""
from __future__ import annotations

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql as pg

revision = "0001_initial"
down_revision = None
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "labor_batch",
        sa.Column("batch_id", sa.String(64), primary_key=True),
        sa.Column("tenant_id", sa.String(128), nullable=False),
        sa.Column("status", sa.String(32), nullable=False, server_default="queued"),
        sa.Column("submitted_count", sa.Integer, server_default="0"),
        sa.Column("failure_policy", sa.String(32), server_default="continue_on_error"),
        sa.Column("concurrency_limit", sa.Integer),
        sa.Column("executor_policy_id", sa.String(64)),
        sa.Column("idempotency_key", sa.String(256), nullable=False),
        sa.Column("cancel_requested", sa.Boolean, server_default=sa.false()),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now()),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.func.now()),
        sa.UniqueConstraint("tenant_id", "idempotency_key", name="uq_batch_tenant_idem"),
    )
    op.create_index("ix_labor_batch_tenant_status", "labor_batch", ["tenant_id", "status"])
    op.create_index("ix_labor_batch_tenant_created", "labor_batch", ["tenant_id", "created_at"])

    op.create_table(
        "labor_job",
        sa.Column("job_id", sa.String(64), primary_key=True),
        sa.Column("tenant_id", sa.String(128), nullable=False),
        sa.Column("workspace_id", sa.String(128)),
        sa.Column("project_id", sa.String(128)),
        sa.Column("cost_center_id", sa.String(128)),
        sa.Column("labor_call_id", sa.String(64)),
        sa.Column("batch_id", sa.String(64), sa.ForeignKey("labor_batch.batch_id")),
        sa.Column("status", sa.String(32), nullable=False, server_default="queued"),
        sa.Column("stage", sa.String(256)),
        sa.Column("executor_used", sa.String(128)),
        sa.Column("progress_percent", sa.Float),
        sa.Column("repair_attempt_count", sa.Integer, server_default="0"),
        sa.Column("validation_status", sa.String(32)),
        sa.Column("request_payload", pg.JSONB, nullable=False),
        sa.Column("failure_reason", sa.Text),
        sa.Column("started_at", sa.DateTime(timezone=True)),
        sa.Column("completed_at", sa.DateTime(timezone=True)),
        sa.Column("cancelled_at", sa.DateTime(timezone=True)),
        sa.Column("cancel_requested", sa.Boolean, server_default=sa.false()),
        sa.Column("pause_requested", sa.Boolean, server_default=sa.false()),
        sa.Column("trace_context", pg.JSONB, nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now()),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.func.now()),
    )
    op.create_index("ix_labor_job_tenant_status", "labor_job", ["tenant_id", "status"])
    op.create_index("ix_labor_job_tenant_created", "labor_job", ["tenant_id", "created_at"])
    op.create_index("ix_labor_job_batch", "labor_job", ["batch_id"])
    op.create_index("ix_labor_job_stage", "labor_job", ["tenant_id", "stage"])

    op.create_table(
        "batch_item",
        sa.Column("id", sa.BigInteger, primary_key=True, autoincrement=True),
        sa.Column("batch_id", sa.String(64), sa.ForeignKey("labor_batch.batch_id"), nullable=False),
        sa.Column("custom_id", sa.String(256), nullable=False),
        sa.Column("labor_call_id", sa.String(64)),
        sa.Column("job_id", sa.String(64)),
        sa.Column("status", sa.String(32), server_default="queued"),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now()),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.func.now()),
        sa.UniqueConstraint("batch_id", "custom_id", name="uq_batch_item_custom"),
    )
    op.create_index("ix_batch_item_batch_status", "batch_item", ["batch_id", "status"])

    op.create_table(
        "artifact_ref",
        sa.Column("artifact_ref_id", sa.String(64), primary_key=True),
        sa.Column("artifact_type", sa.String(64), nullable=False),
        sa.Column("storage_system", sa.String(32), nullable=False, server_default="GATEWAY"),
        sa.Column("version", sa.String(64), nullable=False, server_default="1"),
        sa.Column("tenant_id", sa.String(128), nullable=False),
        sa.Column("job_id", sa.String(64), sa.ForeignKey("labor_job.job_id")),
        sa.Column("labor_call_id", sa.String(64)),
        sa.Column("uri", sa.Text),
        sa.Column("checksum", sa.String(128)),
        sa.Column("data_classification", sa.String(64)),
        sa.Column("immutable", sa.Boolean, server_default=sa.false()),
        sa.Column("content", pg.JSONB),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now()),
    )
    op.create_index("ix_artifact_tenant_type", "artifact_ref", ["tenant_id", "artifact_type"])
    op.create_index("ix_artifact_job", "artifact_ref", ["job_id"])
    op.create_index("ix_artifact_labor_call", "artifact_ref", ["labor_call_id"])

    op.create_table(
        "execution_event",
        sa.Column("event_id", sa.String(64), primary_key=True),
        sa.Column("event_sequence", sa.BigInteger, nullable=False),
        sa.Column("tenant_id", sa.String(128), nullable=False),
        sa.Column("job_id", sa.String(64), nullable=False),
        sa.Column("labor_call_id", sa.String(64)),
        sa.Column("batch_id", sa.String(64)),
        sa.Column("event_type", sa.String(64), nullable=False),
        sa.Column("status", sa.String(32)),
        sa.Column("progress_percent", sa.Float),
        sa.Column("message", sa.Text),
        sa.Column("artifact_refs", pg.JSONB),
        sa.Column("correlation_id", sa.String(64)),
        sa.Column("causation_id", sa.String(64)),
        sa.Column("parent_event_id", sa.String(64)),
        sa.Column("replayable", sa.Boolean, server_default=sa.true()),
        sa.Column("payload", pg.JSONB),
        sa.Column("occurred_at", sa.DateTime(timezone=True), server_default=sa.func.now()),
        sa.Column("trace_id", sa.String(128), nullable=False),
        sa.UniqueConstraint("job_id", "event_sequence", name="uq_event_job_seq"),
    )
    op.create_index("ix_event_tenant_job", "execution_event", ["tenant_id", "job_id"])
    op.create_index("ix_event_job_seq", "execution_event", ["job_id", "event_sequence"])
    op.create_index("ix_event_occurred", "execution_event", ["occurred_at"])

    op.create_table(
        "cost_record",
        sa.Column("cost_record_id", sa.String(64), primary_key=True),
        sa.Column("tenant_id", sa.String(128), nullable=False),
        sa.Column("cost_center_id", sa.String(128)),
        sa.Column("labor_call_id", sa.String(64)),
        sa.Column("batch_id", sa.String(64)),
        sa.Column("amount", sa.Float, server_default="0"),
        sa.Column("currency", sa.String(8), server_default="USD"),
        sa.Column("rate_class", sa.String(16)),
        sa.Column("cache_read_tokens", sa.Integer, server_default="0"),
        sa.Column("cache_write_tokens", sa.Integer, server_default="0"),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now()),
    )
    op.create_index("ix_cost_tenant_center_created", "cost_record", ["tenant_id", "cost_center_id", "created_at"])

    op.create_table(
        "idempotency_key",
        sa.Column("id", sa.BigInteger, primary_key=True, autoincrement=True),
        sa.Column("tenant_id", sa.String(128), nullable=False),
        sa.Column("endpoint", sa.String(64), nullable=False),
        sa.Column("idem_key", sa.String(256), nullable=False),
        sa.Column("resource_id", sa.String(64), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now()),
        sa.UniqueConstraint("tenant_id", "endpoint", "idem_key", name="uq_idem"),
    )

    for name in ("executor_policy", "model_policy"):
        op.create_table(
            name,
            sa.Column(f"{name}_id", sa.String(64), primary_key=True),
            sa.Column("tenant_id", sa.String(128), nullable=False),
            sa.Column("name", sa.String(256)),
            sa.Column("spec", pg.JSONB, nullable=False),
            sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now()),
            sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.func.now()),
        )
        op.create_index(f"ix_{name}_tenant", name, ["tenant_id"])

    # --- append-only enforcement on execution_event ---
    op.execute(
        """
        CREATE OR REPLACE FUNCTION forbid_mutation() RETURNS trigger AS $$
        BEGIN
            RAISE EXCEPTION 'table % is append-only', TG_TABLE_NAME;
        END;
        $$ LANGUAGE plpgsql;
        """
    )
    op.execute(
        """
        CREATE TRIGGER execution_event_append_only
        BEFORE UPDATE OR DELETE ON execution_event
        FOR EACH ROW EXECUTE FUNCTION forbid_mutation();
        """
    )

    # --- raw-output immutability: block UPDATE/DELETE of immutable artifacts ---
    op.execute(
        """
        CREATE OR REPLACE FUNCTION forbid_immutable_artifact_change() RETURNS trigger AS $$
        BEGIN
            IF (TG_OP = 'DELETE') THEN
                IF OLD.immutable THEN
                    RAISE EXCEPTION 'artifact % is immutable', OLD.artifact_ref_id;
                END IF;
                RETURN OLD;
            END IF;
            IF OLD.immutable THEN
                RAISE EXCEPTION 'artifact % is immutable', OLD.artifact_ref_id;
            END IF;
            RETURN NEW;
        END;
        $$ LANGUAGE plpgsql;
        """
    )
    op.execute(
        """
        CREATE TRIGGER artifact_immutable_guard
        BEFORE UPDATE OR DELETE ON artifact_ref
        FOR EACH ROW EXECUTE FUNCTION forbid_immutable_artifact_change();
        """
    )


def downgrade() -> None:
    op.execute("DROP TRIGGER IF EXISTS artifact_immutable_guard ON artifact_ref;")
    op.execute("DROP TRIGGER IF EXISTS execution_event_append_only ON execution_event;")
    op.execute("DROP FUNCTION IF EXISTS forbid_immutable_artifact_change;")
    op.execute("DROP FUNCTION IF EXISTS forbid_mutation;")
    for name in ("model_policy", "executor_policy", "idempotency_key", "cost_record",
                 "execution_event", "artifact_ref", "batch_item", "labor_job", "labor_batch"):
        op.drop_table(name)
