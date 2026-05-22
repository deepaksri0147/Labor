"""Labor orchestration — the execution pipeline behind a labor call.

Pipeline: resolve cache prefixes -> execute on chosen executor -> persist raw (immutable)
-> parse -> validate -> repair loop (bounded) -> persist parsed -> emit events throughout.

The stage label and output_du_class are stored and echoed but never interpreted here.
"""
from __future__ import annotations

import hashlib
import json
import uuid
from typing import Any

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.enums import EventType, JobState, OnParseFailure, RawPersistencePolicy
from app.models import orm
from app.services import infra
from app.services.executors import registry
from app.services.validation import validate_against_schema


def new_id(prefix: str) -> str:
    return f"{prefix}_{uuid.uuid4().hex[:24]}"


def _checksum(text: str) -> str:
    return "sha256:" + hashlib.sha256(text.encode("utf-8")).hexdigest()


async def emit(
    session: AsyncSession,
    job: orm.LaborJob,
    event_type: EventType,
    *,
    message: str | None = None,
    status: JobState | None = None,
    progress: float | None = None,
    artifact_refs: list[dict] | None = None,
) -> None:
    seq = await infra.next_sequence(job.job_id)
    event_id = new_id("evt")
    trace_id = (job.trace_context or {}).get("traceparent", "")
    row = orm.ExecutionEvent(
        event_id=event_id,
        event_sequence=seq,
        tenant_id=job.tenant_id,
        job_id=job.job_id,
        labor_call_id=job.labor_call_id,
        batch_id=job.batch_id,
        event_type=event_type.value,
        status=status.value if status else None,
        progress_percent=progress,
        message=message,
        artifact_refs=artifact_refs,
        trace_id=trace_id,
    )
    session.add(row)
    await session.flush()
    await infra.publish_event(
        job.job_id,
        {
            "event_id": event_id,
            "event_sequence": seq,
            "tenant_id": job.tenant_id,
            "job_id": job.job_id,
            "labor_call_id": job.labor_call_id,
            "batch_id": job.batch_id,
            "event_type": event_type.value,
            "status": status.value if status else None,
            "progress_percent": progress,
            "message": message,
            "artifact_refs": artifact_refs,
            "occurred_at": infra.utcnow().isoformat(),
            "trace_id": trace_id,
        },
    )


async def _persist_artifact(
    session: AsyncSession,
    job: orm.LaborJob,
    *,
    artifact_type: str,
    content: Any,
    immutable: bool,
    classification: str | None,
) -> orm.ArtifactRef:
    text = content if isinstance(content, str) else json.dumps(content, default=str)
    ref = orm.ArtifactRef(
        artifact_ref_id=new_id("art"),
        artifact_type=artifact_type,
        storage_system="GATEWAY",
        version="1",
        tenant_id=job.tenant_id,
        job_id=job.job_id,
        labor_call_id=job.labor_call_id,
        checksum=_checksum(text),
        data_classification=classification,
        immutable=immutable,
        content={"value": content} if not isinstance(content, dict) else content,
    )
    session.add(ref)
    await session.flush()
    return ref


def _artifact_dict(ref: orm.ArtifactRef) -> dict:
    return {
        "artifact_ref_id": ref.artifact_ref_id,
        "artifact_type": ref.artifact_type,
        "storage_system": ref.storage_system,
        "version": ref.version,
        "tenant_id": ref.tenant_id,
        "checksum": ref.checksum,
        "data_classification": ref.data_classification,
    }


def _parse_output(raw: str, mode: str) -> tuple[Any, bool]:
    """Return (parsed, ok). For json mode, attempt to load; otherwise pass through."""
    if mode == "json":
        try:
            return json.loads(raw), True
        except json.JSONDecodeError:
            # try to find the first json object/array substring
            for opener, closer in (("{", "}"), ("[", "]")):
                i, j = raw.find(opener), raw.rfind(closer)
                if i != -1 and j != -1 and j > i:
                    try:
                        return json.loads(raw[i : j + 1]), True
                    except json.JSONDecodeError:
                        pass
            return raw, False
    return raw, True


async def run_labor_job(session: AsyncSession, job: orm.LaborJob) -> None:
    """Execute one labor job to completion. Called by the worker."""
    req = job.request_payload  # the validated LaborCallRequest as a dict
    classification = req.get("data_classification") or req.get("envelope", {}).get("data_classification")

    job.status = JobState.running.value
    job.started_at = infra.utcnow()
    await emit(session, job, EventType.job_started, status=JobState.running, progress=0.0)

    # honor cooperative pause/cancel set between enqueue and pickup
    await session.refresh(job)
    if job.cancel_requested:
        await _finish(session, job, JobState.cancelled, "cancelled before start")
        return

    # 1. choose executor (pluggable backend; identical contract either way)
    et = req["execution_target"]
    try:
        executor = (
            registry.get(et["executor_id"])
            if et.get("kind") == "executor" and et.get("executor_id")
            else registry.choose_auto(data_classification=classification)
        )
    except KeyError as e:
        await _finish(session, job, JobState.failed, f"executor resolution failed: {e}")
        return
    job.executor_used = executor.executor_id

    # 2. resolve cache prefixes (declared, reusable, TTL-bounded)
    cached = await infra.resolve_prefixes(req.get("cache_prefix_ids") or [])

    # 3. build messages
    messages = req.get("messages") or [{"role": "user", "content": json.dumps(req.get("input_data") or {})}]

    # 4. execute on the chosen backend
    await emit(session, job, EventType.labor_started, status=JobState.running, progress=20.0)
    try:
        result = await executor.execute(
            messages=messages, parameters=req.get("inference_parameters") or {}, cached_prefixes=cached
        )
    except Exception as e:  # downstream/inference failure
        await _finish(session, job, JobState.failed, f"execution failed: {e}")
        return

    # 5. persist raw output (immutable per policy)
    raw_ref = None
    if req.get("persist_raw_output", True) and req.get("raw_output_persistence_policy") != RawPersistencePolicy.discard.value:
        raw_ref = await _persist_artifact(
            session, job, artifact_type="raw_model_output", content=result.raw_text,
            immutable=True, classification=classification,
        )
    await emit(session, job, EventType.labor_completed, status=JobState.running, progress=55.0,
               artifact_refs=[_artifact_dict(raw_ref)] if raw_ref else None)

    # record cost / usage
    session.add(orm.CostRecord(
        cost_record_id=new_id("cost"), tenant_id=job.tenant_id,
        cost_center_id=req.get("cost_center_id") or req.get("envelope", {}).get("cost_center_id"),
        labor_call_id=job.labor_call_id, batch_id=job.batch_id,
        rate_class="batch" if job.batch_id else "sync",
        cache_read_tokens=result.cache_read_tokens, cache_write_tokens=result.cache_write_tokens,
    ))

    # 6. parse
    parsed, ok = _parse_output(result.raw_text, req.get("output_parse_mode", "json"))
    if not ok:
        action = req.get("on_parse_failure", OnParseFailure.fail.value)
        if action == OnParseFailure.fail.value:
            await _finish(session, job, JobState.failed, "output parse failed")
            return
        if action == OnParseFailure.hold_for_review.value:
            await _finish(session, job, JobState.waiting_for_review, "parse failed; held for review")
            return
        # mark_candidate / repair fall through with raw text as parsed
    parsed_ref = None
    if req.get("persist_parsed_output", True):
        parsed_ref = await _persist_artifact(
            session, job, artifact_type="parsed_model_output", content=parsed,
            immutable=False, classification=classification,
        )

    # 7. validate (typed errors) + bounded repair loop
    validation_ref = None
    if req.get("validation_required"):
        schema = _resolve_output_schema(req)
        await emit(session, job, EventType.validation_started, status=JobState.validating, progress=70.0)
        job.status = JobState.validating.value
        errors = validate_against_schema(parsed, schema) if schema else []
        max_attempts = (req.get("repair_policy") or {}).get("max_attempts", 0) or 0
        attempt = 0
        while errors and attempt < max_attempts:
            job.status = JobState.repairing.value
            job.repair_attempt_count = attempt + 1
            await emit(session, job, EventType.repair_started, status=JobState.repairing,
                       progress=70.0 + attempt, message=f"repair attempt {attempt + 1}")
            repaired = await _repair(executor, parsed, errors, cached)
            parsed, ok = _parse_output(repaired, req.get("output_parse_mode", "json"))
            errors = validate_against_schema(parsed, schema) if (schema and ok) else errors
            attempt += 1
            await emit(session, job, EventType.repair_completed, status=JobState.repairing,
                       progress=72.0 + attempt)

        status_str = "passed" if not errors else "failed"
        job.validation_status = status_str
        validation_ref = await _persist_artifact(
            session, job, artifact_type="validation_result",
            content={"status": status_str, "errors": [e.model_dump() for e in errors]},
            immutable=False, classification=classification,
        )
        await emit(
            session, job,
            EventType.validation_passed if not errors else EventType.validation_failed,
            status=JobState.validating, progress=85.0,
        )
        if errors and req.get("on_parse_failure") != OnParseFailure.mark_candidate.value:
            # validation still failing after repair budget exhausted -> terminal repair_required
            await _finish(session, job, JobState.repair_required, "validation failed after repair budget")
            return

        # re-persist the (possibly repaired) parsed output
        if req.get("persist_parsed_output", True):
            parsed_ref = await _persist_artifact(
                session, job, artifact_type="parsed_model_output", content=parsed,
                immutable=False, classification=classification,
            )

    # 8. success
    await _finish(session, job, JobState.succeeded, None, progress=100.0)


async def _repair(executor, parsed: Any, errors, cached: list[str]) -> str:
    """Ask the same executor to fix the output given the typed errors."""
    err_lines = "\n".join(f"- {e.path}: {e.rule_id} ({e.repair_hint})" for e in errors)
    prompt = (
        "The following output failed schema validation. Return ONLY the corrected output.\n\n"
        f"OUTPUT:\n{json.dumps(parsed, default=str)}\n\nERRORS:\n{err_lines}"
    )
    res = await executor.execute(
        messages=[{"role": "user", "content": prompt}], parameters={}, cached_prefixes=cached
    )
    return res.raw_text


def _resolve_output_schema(req: dict) -> dict | None:
    osch = req.get("output_schema")
    if isinstance(osch, dict) and "artifact_ref_id" not in osch:
        return osch
    # ArtifactRef form: a real impl dereferences it from the artifact store; inline content
    # is supported here when provided.
    if isinstance(osch, dict) and osch.get("content"):
        return osch["content"]
    return None


async def _finish(session: AsyncSession, job: orm.LaborJob, status: JobState, reason: str | None, progress: float | None = None) -> None:
    job.status = status.value
    job.completed_at = infra.utcnow()
    if status == JobState.cancelled:
        job.cancelled_at = infra.utcnow()
    if reason:
        job.failure_reason = reason
    et = {
        JobState.succeeded: EventType.job_succeeded,
        JobState.failed: EventType.job_failed,
        JobState.cancelled: EventType.job_cancelled,
    }.get(status, EventType.job_progress)
    await emit(session, job, et, status=status, progress=progress, message=reason)


# ---- idempotency ----------------------------------------------------------
async def find_idempotent(session: AsyncSession, tenant_id: str, endpoint: str, key: str) -> str | None:
    row = (await session.execute(
        select(orm.IdempotencyKey.resource_id).where(
            orm.IdempotencyKey.tenant_id == tenant_id,
            orm.IdempotencyKey.endpoint == endpoint,
            orm.IdempotencyKey.idem_key == key,
        )
    )).scalar_one_or_none()
    return row


async def record_idempotent(session: AsyncSession, tenant_id: str, endpoint: str, key: str, resource_id: str) -> None:
    session.add(orm.IdempotencyKey(
        tenant_id=tenant_id, endpoint=endpoint, idem_key=key, resource_id=resource_id
    ))
