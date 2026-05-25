"""Pydantic v2 schemas for the DITA service — mirror dita.additions.json."""
from __future__ import annotations

from datetime import datetime
from typing import Any, Literal

from pydantic import BaseModel, ConfigDict

from app.core.enums import (
    ChainType,
    DocumentJobType,
    JobState,
    SeedPacketStatus,
    StepType,
    WorkflowState,
)


class Base(BaseModel):
    model_config = ConfigDict(use_enum_values=True, extra="forbid")


class TraceContext(Base):
    traceparent: str
    tracestate: str | None = None


class RequestEnvelope(Base):
    tenant_id: str
    workspace_id: str | None = None
    project_id: str | None = None
    initiator_id: str | None = None
    initiator_type: Literal["TENANT", "SYSTEM", "AGENT"] | None = None
    cost_center_id: str | None = None
    policy_scope_id: str | None = None
    data_classification: str | None = None
    trace_context: TraceContext


class ArtifactRef(Base):
    artifact_ref_id: str
    artifact_type: str
    storage_system: Literal["GATEWAY", "DITA", "PI", "CONTENT", "OBJECT_STORE", "ORCHESTRATION", "EXTERNAL"]
    version: str
    tenant_id: str
    uri: str | None = None
    checksum: str | None = None
    data_classification: str | None = None
    created_at: datetime | None = None


# ---- templates ------------------------------------------------------------
class DataTemplate(Base):
    tenant_id: str
    workspace_id: str | None = None
    project_id: str | None = None
    template_id: str | None = None
    name: str
    description: str | None = None
    template_type: str
    input_schema: dict[str, Any]
    output_schema: dict[str, Any]
    required_variables: list[str]
    optional_variables: list[str] | None = None
    default_values: dict[str, Any] | None = None
    validation_schema_ref: ArtifactRef | None = None
    render_target: Literal["dita_topic", "dita_map", "html", "text", "pdf", "json", "markdown", "docx_candidate"]
    llm_required: bool
    status: Literal["draft", "active", "deprecated"]
    version: str
    tags: list[str] | None = None


class PromptTemplate(Base):
    tenant_id: str
    prompt_template_id: str | None = None
    name: str
    description: str | None = None
    prompt_body: str
    system_prompt: str | None = None
    input_schema: dict[str, Any]
    output_schema: dict[str, Any]
    output_format: Literal["json", "text", "markdown", "dita_topic_xml", "dita_map_xml", "html", "mixed"]
    variable_names: list[str]
    allowed_executor_policy_ids: list[str] | None = None
    validation_schema_ref: ArtifactRef | None = None
    repair_template_id: str | None = None
    tags: list[str] | None = None
    version: str
    status: Literal["draft", "active", "deprecated"]


class PromptTemplateRenderRequest(Base):
    data: dict[str, Any]
    context_artifact_refs: list[ArtifactRef] | None = None
    strict_missing_variable_check: bool


class PromptTemplateRenderResponse(Base):
    rendered_prompt: str
    missing_variables: list[str]
    used_variables: dict[str, Any]
    artifact_ref: ArtifactRef | None = None


# ---- chains ---------------------------------------------------------------
class PromptChainStep(Base):
    step_id: str
    step_type: StepType
    data_template_id: str | None = None
    prompt_template_id: str | None = None
    dita_bundle_id: str | None = None
    dita_chunk_ids: list[str] | None = None
    executor_policy_id: str | None = None
    input_mapping: dict[str, Any]
    output_mapping: dict[str, Any]
    validation_schema_ref: ArtifactRef | None = None
    repair_policy_id: str | None = None
    human_approval_required: bool | None = None
    timeout_seconds: int | None = None
    render_config: dict[str, Any] | None = None  # render_document overrides (format, is_global, pdfOptions, bqId, encrypt)


class PromptChainEdge(Base):
    from_step_id: str
    to_step_id: str
    condition: str | None = None


class PromptChain(Base):
    tenant_id: str
    prompt_chain_id: str | None = None
    name: str
    description: str | None = None
    chain_type: ChainType
    steps: list[PromptChainStep]
    edges: list[PromptChainEdge] | None = None
    global_input_schema: dict[str, Any] | None = None
    global_output_schema: dict[str, Any] | None = None
    failure_policy: dict[str, Any]
    max_depth: int | None = None
    version: str
    status: Literal["draft", "active", "deprecated"]


class ChainExecuteRequest(Base):
    envelope: RequestEnvelope
    input_data: dict[str, Any]
    idempotency_key: str
    execution_mode: Literal["sync_preview", "async", "batch"]
    callback_url: str | None = None


class ChainExecuteResponse(Base):
    prompt_chain_run_id: str
    job_id: str
    status: JobState
    events_url: str | None = None
    artifacts_url: str | None = None


# ---- document jobs --------------------------------------------------------
class HydrationBinding(Base):
    source: Literal["etl_job", "synthetic", "manual"]
    etl_job_ref: str | None = None
    synthetic_ref: str | None = None
    reevaluate_on_change: bool = True


class DocumentJobRequest(Base):
    envelope: RequestEnvelope
    job_type: DocumentJobType
    bundle_id: str | None = None
    chunk_ids: list[str] | None = None
    source_document_ref: str | None = None
    target_schema_hint: str | None = None
    data: dict[str, Any] | None = None
    chunk_data: dict[str, Any] | None = None
    format: Literal["html", "text", "pdf", "json", "markdown"]
    prompt_chain_id: str | None = None
    executor_policy_id: str | None = None
    hydration_binding: HydrationBinding | None = None
    idempotency_key: str
    callback_url: str | None = None


class DocumentJob(Base):
    job_id: str
    status: JobState
    tenant_id: str | None = None
    workspace_id: str | None = None
    project_id: str | None = None
    submitted_by: str | None = None
    document_workflow_id: str | None = None
    prompt_chain_run_id: str | None = None
    current_step_id: str | None = None
    progress_percent: float | None = None
    input_artifact_refs: list[ArtifactRef] | None = None
    output_artifact_refs: list[ArtifactRef] | None = None
    validation_result_refs: list[ArtifactRef] | None = None
    repair_result_refs: list[ArtifactRef] | None = None
    input_refs: list[ArtifactRef] | None = None
    output_refs: list[ArtifactRef] | None = None
    events_url: str | None = None
    artifacts_url: str | None = None
    lineage_url: str | None = None
    cancel_url: str | None = None
    created_at: datetime | None = None
    trace_context: dict[str, Any] | None = None


class DocumentJobArtifacts(Base):
    job_id: str
    artifacts: list[ArtifactRef] | None = None
    rendered_outputs: list[ArtifactRef] | None = None
    generated_documents: list[ArtifactRef] | None = None
    validation_results: list[ArtifactRef] | None = None
    repair_results: list[ArtifactRef] | None = None


# ---- document workflow + feedback ----------------------------------------
class SubmitReviewRequest(Base):
    reviewer_ids: list[str] | None = None
    review_policy_id: str | None = None
    comment: str | None = None


class ApproveRequest(Base):
    approval_comment: str | None = None
    publish_after_approval: bool | None = None
    signature_ref: str | None = None


class RejectRequest(Base):
    rejection_reason: str
    create_repair_request: bool


class RequestRepairRequest(Base):
    repair_reason: str
    failed_sections: list[str] | None = None
    feedback_event_refs: list[ArtifactRef] | None = None
    repair_policy_id: str | None = None


class PublishRequest(Base):
    publication_channel: str
    immutable: bool
    effective_at: datetime | None = None


class RollbackRequest(Base):
    target_version: str
    rollback_reason: str


class FeedbackEvent(Base):
    feedback_type: Literal[
        "human_review", "validation_failure", "model_self_critique", "policy_violation",
        "domain_correction", "legal_redline", "customer_feedback", "approval_comment",
    ]
    section_ref: str | None = None
    comment: str
    severity: Literal["low", "medium", "high", "critical"]
    suggested_action: Literal["ignore", "annotate", "repair", "regenerate", "escalate", "reject", "approve"] | None = None
    attached_artifact_refs: list[ArtifactRef] | None = None


# ---- seed packets ---------------------------------------------------------
class SeedPacket(Base):
    seed_packet_id: str | None = None
    tenant_id: str
    source_document_ref: ArtifactRef | None = None
    data_template_id: str
    prompt_template_id: str | None = None
    prompt_chain_id: str | None = None
    bundle_id: str | None = None
    chunk_ids: list[str] | None = None
    input_data: dict[str, Any]
    output_artifact_type: str
    validation_schema_ref: ArtifactRef | None = None
    status: SeedPacketStatus = SeedPacketStatus.draft
    lineage_refs: list[ArtifactRef] | None = None
    version: str
    document_id: str | None = None
    document_workflow_id: str | None = None
    prompt_chain_run_id: str | None = None
    labor_call_id: str | None = None
    job_id: str | None = None
    reentry_required: bool
    reentry_reason: Literal[
        "none", "new_claim", "new_binding", "new_object", "repair_needed",
        "certification_needed", "manual_review",
    ] | None = None
    rendered_artifact_ref: str | None = None   # set after POST /render
    render_format: str | None = None           # carried across render calls


class SeedPacketStatusUpdate(Base):
    status: SeedPacketStatus
    reason: str | None = None
    validation_result_ref: ArtifactRef | None = None
    repair_result_ref: ArtifactRef | None = None
    updated_by: str | None = None


class JobEvent(Base):
    event_id: str
    event_sequence: int
    tenant_id: str
    job_id: str
    event_type: str
    status: JobState | None = None
    progress_percent: float | None = None
    message: str | None = None
    artifact_refs: list[ArtifactRef] | None = None
    occurred_at: datetime
    trace_id: str
