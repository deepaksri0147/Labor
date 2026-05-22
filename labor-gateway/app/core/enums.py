"""Shared enums that match the OpenAPI fragment exactly."""
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
    JobState.succeeded,
    JobState.failed,
    JobState.cancelled,
    JobState.timed_out,
    JobState.rolled_back,
    JobState.archived,
    JobState.repair_required,
}


class StorageSystem(str, enum.Enum):
    GATEWAY = "GATEWAY"
    DITA = "DITA"
    PI = "PI"
    CONTENT = "CONTENT"
    OBJECT_STORE = "OBJECT_STORE"
    ORCHESTRATION = "ORCHESTRATION"
    EXTERNAL = "EXTERNAL"


class FailurePolicy(str, enum.Enum):
    fail_fast = "fail_fast"
    continue_on_error = "continue_on_error"
    retry_failed = "retry_failed"
    repair_failed = "repair_failed"
    hold_for_review = "hold_for_review"


class OnParseFailure(str, enum.Enum):
    fail = "fail"
    repair = "repair"
    mark_candidate = "mark_candidate"
    hold_for_review = "hold_for_review"


class RawPersistencePolicy(str, enum.Enum):
    immutable = "immutable"
    discard = "discard"
    encrypted = "encrypted"


class ParsedPersistencePolicy(str, enum.Enum):
    persist = "persist"
    discard = "discard"


class EventType(str, enum.Enum):
    job_submitted = "JOB_SUBMITTED"
    job_started = "JOB_STARTED"
    job_progress = "JOB_PROGRESS"
    labor_started = "LABOR_STARTED"
    labor_completed = "LABOR_COMPLETED"
    validation_started = "VALIDATION_STARTED"
    validation_passed = "VALIDATION_PASSED"
    validation_failed = "VALIDATION_FAILED"
    repair_started = "REPAIR_STARTED"
    repair_completed = "REPAIR_COMPLETED"
    job_paused = "JOB_PAUSED"
    job_resumed = "JOB_RESUMED"
    job_cancel_requested = "JOB_CANCEL_REQUESTED"
    job_cancelled = "JOB_CANCELLED"
    job_failed = "JOB_FAILED"
    job_succeeded = "JOB_SUCCEEDED"
