"""initial dita service schema

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


def _ts(*cols):
    return [
        *cols,
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now()),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.func.now()),
    ]


def upgrade() -> None:
    op.create_table(
        "versioned_asset",
        *_ts(
            sa.Column("asset_id", sa.String(64), primary_key=True),
            sa.Column("asset_kind", sa.String(32), nullable=False),
            sa.Column("tenant_id", sa.String(128), nullable=False),
            sa.Column("name", sa.String(256), nullable=False),
            sa.Column("version", sa.String(64), nullable=False, server_default="1"),
            sa.Column("status", sa.String(32), server_default="draft"),
            sa.Column("spec", pg.JSONB, nullable=False),
        ),
    )
    op.create_index("ix_asset_tenant_kind_status", "versioned_asset", ["tenant_id", "asset_kind", "status"])

    op.create_table(
        "asset_version",
        sa.Column("id", sa.BigInteger, primary_key=True, autoincrement=True),
        sa.Column("asset_id", sa.String(64), nullable=False),
        sa.Column("asset_kind", sa.String(32), nullable=False),
        sa.Column("version", sa.String(64), nullable=False),
        sa.Column("spec", pg.JSONB, nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now()),
        sa.UniqueConstraint("asset_id", "version", name="uq_asset_version"),
    )
    op.create_index("ix_asset_version_asset", "asset_version", ["asset_id"])

    op.create_table(
        "document_job",
        *_ts(
            sa.Column("job_id", sa.String(64), primary_key=True),
            sa.Column("tenant_id", sa.String(128), nullable=False),
            sa.Column("workspace_id", sa.String(128)),
            sa.Column("project_id", sa.String(128)),
            sa.Column("submitted_by", sa.String(128)),
            sa.Column("job_type", sa.String(40), nullable=False),
            sa.Column("status", sa.String(32), server_default="queued"),
            sa.Column("document_workflow_id", sa.String(64)),
            sa.Column("prompt_chain_run_id", sa.String(64)),
            sa.Column("current_step_id", sa.String(64)),
            sa.Column("progress_percent", sa.Float),
            sa.Column("request_payload", pg.JSONB, nullable=False),
            sa.Column("failure_reason", sa.Text),
            sa.Column("cancel_requested", sa.Boolean, server_default=sa.false()),
            sa.Column("pause_requested", sa.Boolean, server_default=sa.false()),
            sa.Column("trace_context", pg.JSONB, nullable=False),
        ),
    )
    op.create_index("ix_docjob_tenant_status", "document_job", ["tenant_id", "status"])
    op.create_index("ix_docjob_tenant_created", "document_job", ["tenant_id", "created_at"])

    op.create_table(
        "prompt_chain_run",
        *_ts(
            sa.Column("prompt_chain_run_id", sa.String(64), primary_key=True),
            sa.Column("tenant_id", sa.String(128), nullable=False),
            sa.Column("prompt_chain_id", sa.String(64), nullable=False),
            sa.Column("job_id", sa.String(64)),
            sa.Column("status", sa.String(32), server_default="queued"),
            sa.Column("current_step_id", sa.String(64)),
            sa.Column("depth", sa.Integer, server_default="0"),
            sa.Column("input_data", pg.JSONB, nullable=False),
            sa.Column("step_outputs", pg.JSONB, server_default="{}"),
            sa.Column("cancel_requested", sa.Boolean, server_default=sa.false()),
            sa.Column("failure_reason", sa.Text),
        ),
    )
    op.create_index("ix_chainrun_tenant_status", "prompt_chain_run", ["tenant_id", "status"])
    op.create_index("ix_chainrun_chain", "prompt_chain_run", ["prompt_chain_id"])

    op.create_table(
        "document_workflow",
        *_ts(
            sa.Column("document_id", sa.String(64), primary_key=True),
            sa.Column("tenant_id", sa.String(128), nullable=False),
            sa.Column("title", sa.String(512)),
            sa.Column("document_type", sa.String(64)),
            sa.Column("bundle_id", sa.String(64)),
            sa.Column("current_version", sa.String(64), server_default="1"),
            sa.Column("workflow_state", sa.String(32), server_default="draft"),
            sa.Column("refs", pg.JSONB, server_default="{}"),
            sa.Column("version", sa.Integer, server_default="1"),
        ),
    )
    op.create_index("ix_docwf_tenant_state", "document_workflow", ["tenant_id", "workflow_state"])
    op.create_index("ix_docwf_tenant_type", "document_workflow", ["tenant_id", "document_type"])

    op.create_table(
        "document_version",
        sa.Column("id", sa.BigInteger, primary_key=True, autoincrement=True),
        sa.Column("document_id", sa.String(64), nullable=False),
        sa.Column("version", sa.String(64), nullable=False),
        sa.Column("published_artifact_ref", sa.String(64)),
        sa.Column("immutable", sa.Boolean, server_default=sa.false()),
        sa.Column("snapshot", pg.JSONB, nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now()),
        sa.UniqueConstraint("document_id", "version", name="uq_doc_version"),
    )
    op.create_index("ix_docver_doc", "document_version", ["document_id"])

    op.create_table(
        "seed_packet",
        *_ts(
            sa.Column("seed_packet_id", sa.String(64), primary_key=True),
            sa.Column("tenant_id", sa.String(128), nullable=False),
            sa.Column("data_template_id", sa.String(64), nullable=False),
            sa.Column("status", sa.String(32), server_default="draft"),
            sa.Column("document_workflow_id", sa.String(64)),
            sa.Column("prompt_chain_run_id", sa.String(64)),
            sa.Column("reentry_required", sa.Boolean, server_default=sa.false()),
            sa.Column("spec", pg.JSONB, nullable=False),
            sa.Column("version", sa.String(64), server_default="1"),
        ),
    )
    op.create_index("ix_seed_tenant_status", "seed_packet", ["tenant_id", "status"])
    op.create_index("ix_seed_template", "seed_packet", ["data_template_id"])
    op.create_index("ix_seed_workflow", "seed_packet", ["document_workflow_id"])
    op.create_index("ix_seed_chainrun", "seed_packet", ["prompt_chain_run_id"])

    op.create_table(
        "feedback_event",
        sa.Column("feedback_id", sa.String(64), primary_key=True),
        sa.Column("tenant_id", sa.String(128), nullable=False),
        sa.Column("document_id", sa.String(64), nullable=False),
        sa.Column("feedback_type", sa.String(40), nullable=False),
        sa.Column("severity", sa.String(16), nullable=False),
        sa.Column("comment", sa.Text, nullable=False),
        sa.Column("spec", pg.JSONB, nullable=False),
        sa.Column("resolved", sa.Boolean, server_default=sa.false()),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now()),
    )
    op.create_index("ix_feedback_tenant_doc", "feedback_event", ["tenant_id", "document_id", "severity"])

    op.create_table(
        "artifact_ref",
        sa.Column("artifact_ref_id", sa.String(64), primary_key=True),
        sa.Column("artifact_type", sa.String(64), nullable=False),
        sa.Column("storage_system", sa.String(32), server_default="DITA"),
        sa.Column("version", sa.String(64), server_default="1"),
        sa.Column("tenant_id", sa.String(128), nullable=False),
        sa.Column("job_id", sa.String(64)),
        sa.Column("prompt_chain_run_id", sa.String(64)),
        sa.Column("document_id", sa.String(64)),
        sa.Column("uri", sa.Text),
        sa.Column("checksum", sa.String(128)),
        sa.Column("data_classification", sa.String(64)),
        sa.Column("immutable", sa.Boolean, server_default=sa.false()),
        sa.Column("content", pg.JSONB),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now()),
    )
    op.create_index("ix_dita_artifact_tenant_type", "artifact_ref", ["tenant_id", "artifact_type"])
    op.create_index("ix_dita_artifact_job", "artifact_ref", ["job_id"])
    op.create_index("ix_dita_artifact_doc", "artifact_ref", ["document_id"])

    op.create_table(
        "lineage_edge",
        sa.Column("lineage_edge_id", sa.String(64), primary_key=True),
        sa.Column("tenant_id", sa.String(128), nullable=False),
        sa.Column("from_artifact_ref", sa.String(64), nullable=False),
        sa.Column("to_artifact_ref", sa.String(64), nullable=False),
        sa.Column("edge_type", sa.String(32), nullable=False),
        sa.Column("job_id", sa.String(64)),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now()),
    )
    op.create_index("ix_dita_lineage_from", "lineage_edge", ["from_artifact_ref"])
    op.create_index("ix_dita_lineage_to", "lineage_edge", ["to_artifact_ref"])

    op.create_table(
        "execution_event",
        sa.Column("event_id", sa.String(64), primary_key=True),
        sa.Column("event_sequence", sa.BigInteger, nullable=False),
        sa.Column("tenant_id", sa.String(128), nullable=False),
        sa.Column("job_id", sa.String(64), nullable=False),
        sa.Column("event_type", sa.String(64), nullable=False),
        sa.Column("status", sa.String(32)),
        sa.Column("progress_percent", sa.Float),
        sa.Column("message", sa.Text),
        sa.Column("artifact_refs", pg.JSONB),
        sa.Column("occurred_at", sa.DateTime(timezone=True), server_default=sa.func.now()),
        sa.Column("trace_id", sa.String(128), nullable=False),
        sa.UniqueConstraint("job_id", "event_sequence", name="uq_dita_event_job_seq"),
    )
    op.create_index("ix_dita_event_job", "execution_event", ["job_id", "event_sequence"])

    op.create_table(
        "idempotency_key",
        sa.Column("id", sa.BigInteger, primary_key=True, autoincrement=True),
        sa.Column("tenant_id", sa.String(128), nullable=False),
        sa.Column("endpoint", sa.String(64), nullable=False),
        sa.Column("idem_key", sa.String(256), nullable=False),
        sa.Column("resource_id", sa.String(64), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now()),
        sa.UniqueConstraint("tenant_id", "endpoint", "idem_key", name="uq_dita_idem"),
    )

    # append-only execution_event
    op.execute(
        """
        CREATE OR REPLACE FUNCTION dita_forbid_mutation() RETURNS trigger AS $$
        BEGIN RAISE EXCEPTION 'table % is append-only', TG_TABLE_NAME; END;
        $$ LANGUAGE plpgsql;
        """
    )
    op.execute(
        "CREATE TRIGGER execution_event_append_only BEFORE UPDATE OR DELETE ON execution_event "
        "FOR EACH ROW EXECUTE FUNCTION dita_forbid_mutation();"
    )
    # immutable artifacts (published documents, raw inputs) cannot be changed/deleted
    op.execute(
        """
        CREATE OR REPLACE FUNCTION dita_forbid_immutable() RETURNS trigger AS $$
        BEGIN
            IF (TG_OP = 'DELETE') THEN
                IF OLD.immutable THEN RAISE EXCEPTION 'artifact % is immutable', OLD.artifact_ref_id; END IF;
                RETURN OLD;
            END IF;
            IF OLD.immutable THEN RAISE EXCEPTION 'artifact % is immutable', OLD.artifact_ref_id; END IF;
            RETURN NEW;
        END;
        $$ LANGUAGE plpgsql;
        """
    )
    op.execute(
        "CREATE TRIGGER artifact_immutable_guard BEFORE UPDATE OR DELETE ON artifact_ref "
        "FOR EACH ROW EXECUTE FUNCTION dita_forbid_immutable();"
    )


def downgrade() -> None:
    op.execute("DROP TRIGGER IF EXISTS artifact_immutable_guard ON artifact_ref;")
    op.execute("DROP TRIGGER IF EXISTS execution_event_append_only ON execution_event;")
    op.execute("DROP FUNCTION IF EXISTS dita_forbid_immutable;")
    op.execute("DROP FUNCTION IF EXISTS dita_forbid_mutation;")
    for t in ("idempotency_key", "execution_event", "lineage_edge", "artifact_ref", "feedback_event",
              "seed_packet", "document_version", "document_workflow", "prompt_chain_run",
              "document_job", "asset_version", "versioned_asset"):
        op.drop_table(t)
