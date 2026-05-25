"""Shared and DITA-specific enums, matching the fragment exactly."""
from __future__ import annotations

import enum


class JobState(str, enum.Enum):
    queued = "queued"
    scheduled = "scheduled"
    running = "running"
    waiting_for_dependency = "waiting_for_dependency"
    waiting_for_review = "waiting_for_review"
    validating = "validating"
    repairing = "repairing"
    repair_required = "repair_required"
    paused = "paused"
    retrying = "retrying"
    partially_completed = "partially_completed"
    succeeded = "succeeded"
    failed = "failed"
    cancelled = "cancelled"
    timed_out = "timed_out"
    rolled_back = "rolled_back"
    archived = "archived"


TERMINAL_STATES = {
    JobState.succeeded, JobState.failed, JobState.cancelled, JobState.timed_out,
    JobState.rolled_back, JobState.archived, JobState.repair_required,
}


class WorkflowState(str, enum.Enum):
    draft = "draft"
    ready_for_generation = "ready_for_generation"
    generation_running = "generation_running"
    generated = "generated"
    validation_running = "validation_running"
    validation_failed = "validation_failed"
    repair_required = "repair_required"
    repair_running = "repair_running"
    ready_for_review = "ready_for_review"
    in_review = "in_review"
    approved = "approved"
    rejected = "rejected"
    published = "published"
    archived = "archived"
    superseded = "superseded"
    rolled_back = "rolled_back"
    cancelled = "cancelled"


# allowed workflow transitions (state machine guard)
WORKFLOW_TRANSITIONS: dict[WorkflowState, set[WorkflowState]] = {
    WorkflowState.draft: {WorkflowState.ready_for_generation, WorkflowState.cancelled},
    WorkflowState.ready_for_generation: {WorkflowState.generation_running, WorkflowState.cancelled},
    WorkflowState.generation_running: {WorkflowState.generated, WorkflowState.validation_failed, WorkflowState.cancelled},
    WorkflowState.generated: {WorkflowState.validation_running, WorkflowState.ready_for_review, WorkflowState.cancelled},
    WorkflowState.validation_running: {WorkflowState.ready_for_review, WorkflowState.validation_failed},
    WorkflowState.validation_failed: {WorkflowState.repair_required, WorkflowState.rejected},
    WorkflowState.repair_required: {WorkflowState.repair_running, WorkflowState.rejected},
    WorkflowState.repair_running: {WorkflowState.generated, WorkflowState.validation_failed},
    WorkflowState.ready_for_review: {WorkflowState.in_review, WorkflowState.approved, WorkflowState.rejected},
    WorkflowState.in_review: {WorkflowState.approved, WorkflowState.rejected, WorkflowState.repair_required},
    WorkflowState.approved: {WorkflowState.published, WorkflowState.rolled_back},
    WorkflowState.rejected: {WorkflowState.repair_required, WorkflowState.archived},
    WorkflowState.published: {WorkflowState.rolled_back, WorkflowState.superseded, WorkflowState.archived},
    WorkflowState.rolled_back: {WorkflowState.ready_for_review, WorkflowState.archived},
    WorkflowState.superseded: {WorkflowState.archived},
}


class ChainType(str, enum.Enum):
    linear = "linear"
    dag = "dag"
    conditional = "conditional"
    repair_loop = "repair_loop"
    approval_loop = "approval_loop"
    batch = "batch"
    recursive = "recursive"


class StepType(str, enum.Enum):
    render_template = "render_template"
    call_llm = "call_llm"
    validate_output = "validate_output"
    repair_output = "repair_output"
    persist_output = "persist_output"
    render_document = "render_document"
    request_human_review = "request_human_review"
    branch = "branch"
    join = "join"
    emit_event = "emit_event"
    call_external_api = "call_external_api"


class DocumentJobType(str, enum.Enum):
    render_bundle = "render_bundle"
    render_chunks = "render_chunks"
    render_and_call_llm = "render_and_call_llm"
    decompose_source = "decompose_source"
    project_to_schema = "project_to_schema"
    generate_document = "generate_document"
    validate_document = "validate_document"
    repair_document = "repair_document"
    publish_document = "publish_document"
    export_document = "export_document"


class SeedPacketStatus(str, enum.Enum):
    draft = "draft"
    ready = "ready"
    rendering = "rendering"
    rendered = "rendered"
    sent_to_llm = "sent_to_llm"
    generated = "generated"
    validation_failed = "validation_failed"
    repair_required = "repair_required"
    approved = "approved"
    rejected = "rejected"
    archived = "archived"
    open_gap = "open_gap"


class EventType(str, enum.Enum):
    job_submitted = "JOB_SUBMITTED"
    job_started = "JOB_STARTED"
    job_progress = "JOB_PROGRESS"
    step_started = "STEP_STARTED"
    step_completed = "STEP_COMPLETED"
    document_rendered = "DOCUMENT_RENDERED"
    schema_projected = "SCHEMA_PROJECTED"
    hydration_started = "HYDRATION_STARTED"
    hydration_completed = "HYDRATION_COMPLETED"
    document_review_requested = "DOCUMENT_REVIEW_REQUESTED"
    document_approved = "DOCUMENT_APPROVED"
    document_rejected = "DOCUMENT_REJECTED"
    document_published = "DOCUMENT_PUBLISHED"
    feedback_added = "FEEDBACK_ADDED"
    job_cancelled = "JOB_CANCELLED"
    job_failed = "JOB_FAILED"
    job_succeeded = "JOB_SUCCEEDED"
    job_paused = "JOB_PAUSED"
    job_resumed = "JOB_RESUMED"
