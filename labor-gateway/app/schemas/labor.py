"""Pydantic v2 request/response models — the wire contract.

These mirror llm-gateway.additions.json one-to-one. Field names and enums match the
fragment so the generated OpenAPI equals the hand-written fragment.
"""
from __future__ import annotations

from datetime import datetime
from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field

from app.core.enums import (
    FailurePolicy,
    JobState,
    OnParseFailure,
    ParsedPersistencePolicy,
    RawPersistencePolicy,
    StorageSystem,
)


class Base(BaseModel):
    model_config = ConfigDict(use_enum_values=True, extra="forbid")


# ---- shared ---------------------------------------------------------------
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
    storage_system: StorageSystem
    version: str
    tenant_id: str
    uri: str | None = None
    checksum: str | None = None
    data_classification: str | None = None
    created_at: datetime | None = None
    content: Any | None = None


class ExecutionTarget(Base):
    kind: Literal["executor", "auto"]
    executor_id: str | None = None
    fallback_executor_id: str | None = None


class RepairPolicy(Base):
    max_attempts: int = 0
    repair_template_id: str | None = None
    preserve_valid_sections: bool = True


class Message(Base):
    role: str
    content: str


class Usage(Base):
    prompt_tokens: int = 0
    completion_tokens: int = 0
    total_tokens: int = 0
    cache_read_tokens: int = 0
    cache_write_tokens: int = 0
    rate_class: Literal["sync", "batch"] | None = None


class Cost(Base):
    amount: float = 0.0
    currency: str = "USD"
    rate_class: Literal["sync", "batch"] | None = None


class ValidationError(Base):
    path: str
    rule_id: str
    severity: Literal["error", "warning", "info"]
    observed: Any | None = None
    expected: Any | None = None
    repair_hint: str | None = None


class Error(Base):
    error_code: str
    message: str
    details: dict[str, Any] | None = None
    retryable: bool | None = None
    validation_errors: list[ValidationError] | None = None
    trace_id: str | None = None


# ---- labor call -----------------------------------------------------------
class LaborCallRequest(Base):
    envelope: RequestEnvelope
    labor_call_id: str | None = None
    labor_template_id: str
    data_template_ref: ArtifactRef
    output_du_class: str
    stage: str | None = None
    output_type: str | None = None
    execution_target: ExecutionTarget
    input_data: dict[str, Any] | None = None
    input_schema_ref: ArtifactRef | None = None
    output_schema: dict[str, Any] | ArtifactRef
    messages: list[Message] | None = None
    cache_prefix_ids: list[str] | None = None
    inference_parameters: dict[str, Any] | None = None
    prompt_template_ref: ArtifactRef | None = None
    prompt_chain_id: str | None = None
    prompt_step_id: str | None = None
    parent_labor_call_id: str | None = None
    upstream_artifact_refs: list[ArtifactRef] | None = None
    downstream_expected_artifact_types: list[str] | None = None
    structured_output_required: bool = True
    validation_required: bool = False
    validation_schema_ref: ArtifactRef | None = None
    repair_policy: RepairPolicy | None = None
    executor_policy_id: str | None = None
    model_policy_id: str | None = None
    data_classification: str | None = None
    retention_policy_id: str | None = None
    cost_center_id: str | None = None
    idempotency_key: str
    priority: Literal["low", "normal", "high"] | None = None
    deadline_at: datetime | None = None
    callback_url: str | None = None
    raw_output_persistence_policy: RawPersistencePolicy
    parsed_output_persistence_policy: ParsedPersistencePolicy
    output_parse_mode: Literal["json", "text", "markdown", "dita_topic_xml", "dita_map_xml", "mixed"]
    json_schema_strict: bool = True
    allow_partial_json_repair: bool = False
    max_parse_attempts: int = 1
    on_parse_failure: OnParseFailure = OnParseFailure.fail
    persist_raw_output: bool = True
    persist_parsed_output: bool = True
    metadata: dict[str, Any] | None = None


class LaborCallResponse(Base):
    labor_call_id: str
    job_id: str | None = None
    status: JobState
    stage: str | None = None
    raw_output_ref: ArtifactRef | None = None
    parsed_output_ref: ArtifactRef | None = None
    validation_result_ref: ArtifactRef | None = None
    repair_result_refs: list[ArtifactRef] | None = None
    output_artifact_refs: list[ArtifactRef] | None = None
    usage: Usage | None = None
    cost: Cost | None = None
    executor_used: str | None = None
    started_at: datetime | None = None
    completed_at: datetime | None = None
    trace_context: dict[str, Any] | None = None
    error: Error | None = None


# ---- jobs -----------------------------------------------------------------
class LaborJob(Base):
    job_id: str
    tenant_id: str
    labor_call_id: str | None = None
    batch_id: str | None = None
    status: JobState
    stage: str | None = None
    executor_used: str | None = None
    progress_percent: float | None = None
    input_refs: list[ArtifactRef] | None = None
    output_refs: list[ArtifactRef] | None = None
    validation_result_ref: ArtifactRef | None = None
    repair_result_refs: list[ArtifactRef] | None = None
    created_at: datetime
    started_at: datetime | None = None
    completed_at: datetime | None = None
    cancelled_at: datetime | None = None
    failure_reason: str | None = None
    trace_context: dict[str, Any]


class JobCancelRequest(Base):
    reason: str
    preserve_partial_outputs: bool
    cancel_downstream: bool


class JobRetryRequest(Base):
    retry_scope: Literal["whole_job", "failed_steps_only", "from_step", "selected_steps"]
    from_step_id: str | None = None
    selected_step_ids: list[str] | None = None
    preserve_successful_steps: bool
    reason: str


class JobForkRequest(Base):
    fork_name: str
    override_input_data: dict[str, Any] | None = None
    reason: str


class LaborJobUxState(Base):
    job_id: str
    status: JobState
    progress_percent: float | None = None
    current_step: str | None = None
    executor_used: str | None = None
    tokens_input: int | None = None
    tokens_output: int | None = None
    cost_so_far: float | None = None
    estimated_remaining_cost: float | None = None
    partial_output_preview: str | None = None
    validation_status: str | None = None
    repair_attempt_count: int | None = None
    can_cancel: bool = False
    can_retry: bool = False
    can_fork: bool = False
    can_approve: bool = False


class JobEvent(Base):
    event_id: str
    event_sequence: int
    tenant_id: str
    job_id: str
    labor_call_id: str | None = None
    batch_id: str | None = None
    event_type: str
    status: JobState | None = None
    progress_percent: float | None = None
    message: str | None = None
    artifact_refs: list[ArtifactRef] | None = None
    correlation_id: str | None = None
    causation_id: str | None = None
    parent_event_id: str | None = None
    replayable: bool | None = None
    occurred_at: datetime
    trace_id: str


class EmitEventResponse(Base):
    accepted: bool
    event_id: str
    event_sequence: int


# ---- batch ----------------------------------------------------------------
class LaborBatchItem(Base):
    custom_id: str
    call: LaborCallRequest


class LaborBatchRequest(Base):
    envelope: RequestEnvelope
    items: list[LaborBatchItem]
    concurrency_limit: int | None = None
    failure_policy: FailurePolicy = FailurePolicy.continue_on_error
    executor_policy_id: str | None = None
    idempotency_key: str
    callback_url: str | None = None


class LaborBatchResponse(Base):
    batch_id: str
    status: JobState
    submitted_count: int | None = None
    counts_by_state: dict[str, int] | None = None
    cost_so_far: Cost | None = None
    events_url: str | None = None
    items_url: str | None = None
    cancel_url: str | None = None
    created_at: datetime | None = None


class LaborBatchItemResult(Base):
    custom_id: str
    labor_call_id: str | None = None
    status: JobState
    result: LaborCallResponse | None = None


# ---- validation / repair --------------------------------------------------
class ValidationRequest(Base):
    validation_schema_ref: ArtifactRef
    parsed_output_ref: ArtifactRef
    validation_policy: dict[str, Any] | None = None


class ValidationResult(Base):
    validation_result_id: str
    status: Literal["passed", "failed"]
    errors: list[ValidationError] | None = None
    warnings: list[ValidationError] | None = None
    repair_required: bool | None = None
    validation_result_ref: ArtifactRef | None = None


class RepairRequest(Base):
    validation_result_ref: ArtifactRef
    repair_template_id: str | None = None
    preserve_valid_sections: bool = True


# ---- cache prefix ---------------------------------------------------------
class CachePrefixCreateRequest(Base):
    tenant_id: str
    content: str
    ttl_seconds: int
    name: str | None = None
    role: Literal["system", "user"] = "system"


class CachePrefix(Base):
    cache_prefix_id: str
    name: str | None = None
    ttl_seconds: int
    token_count: int
    role: Literal["system", "user"] | None = None
    hit_count: int | None = None
    created_at: datetime | None = None
    expires_at: datetime | None = None


# ---- executors / policies -------------------------------------------------
class ExecutorDescriptor(Base):
    executor_id: str
    display_name: str | None = None
    supports_batch: bool | None = None
    supports_cache_prefix: bool | None = None
    status: Literal["available", "degraded", "unavailable"] | None = None


class ExecutorPolicy(Base):
    executor_policy_id: str | None = None
    tenant_id: str
    name: str
    allowed_executors: list[str]
    blocked_executors: list[str] | None = None
    classification_rules: list[dict[str, Any]] | None = None
    max_cost_per_call: float | None = None
    budget: dict[str, Any] | None = None


class ModelPolicy(Base):
    model_policy_id: str | None = None
    tenant_id: str
    name: str | None = None
    allowed_providers: list[str] | None = None
    allowed_models: list[str] | None = None
    blocked_models: list[str] | None = None
    external_model_allowed: bool | None = None
    local_model_required_for: list[str] | None = None
    validation_required_for: list[str] | None = None
    human_approval_required_for: list[str] | None = None
    max_tokens: int | None = None
    max_cost_per_call: float | None = None
    data_class_rules: list[dict[str, Any]] | None = None
