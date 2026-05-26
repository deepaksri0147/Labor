"""Labor job lifecycle: get/cancel/retry/fork/pause/resume/artifacts/lineage/ux-state/
events (SSE)/emit-event.

Persistence delegated to the external data wrapper service.
"""
from __future__ import annotations

import json

from fastapi import APIRouter, Depends, HTTPException, Request, status
from fastapi.responses import StreamingResponse

from app.core.auth import Principal, current_principal, enforce_tenant
from app.core.enums import TERMINAL_STATES, EventType, JobState
from app.models import orm
from app.schemas.labor import (
    EmitEventResponse,
    JobCancelRequest,
    JobEvent,
    JobForkRequest,
    JobRetryRequest,
    LaborJob as LaborJobSchema,
    LaborJobUxState,
)
from app.services import wrapper_api, infra, orchestrator

router = APIRouter(prefix="/labor/jobs", tags=["Labor Jobs"])


async def _get_job_row(principal: Principal, job_id: str) -> dict:
    row = await wrapper_api.retrieve_one(wrapper_api.LABOR_JOB, {"job_id": job_id})
    if not row:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "job not found")
    enforce_tenant(principal, row["tenant_id"])
    return row


@router.get("/{job_id}", response_model=LaborJobSchema)
async def get_job(job_id: str, principal: Principal = Depends(current_principal)) -> LaborJobSchema:
    return _row_to_schema(await _get_job_row(principal, job_id))


@router.post("/{job_id}/cancel", response_model=LaborJobSchema)
async def cancel_job(job_id: str, body: JobCancelRequest,
                     principal: Principal = Depends(current_principal)) -> LaborJobSchema:
    row = await _get_job_row(principal, job_id)
    if JobState(row["status"]) in TERMINAL_STATES:
        raise HTTPException(status.HTTP_409_CONFLICT, f"cannot cancel job in terminal state {row['status']}")
    row["cancel_requested"] = True
    if JobState(row["status"]) in {JobState.queued, JobState.scheduled, JobState.paused}:
        # not yet running: cancel immediately
        await orchestrator.finish_row(row, JobState.cancelled, body.reason)
    else:
        await wrapper_api.update(wrapper_api.LABOR_JOB, [row])
        await orchestrator.emit_row(row, EventType.job_cancel_requested, status=JobState(row["status"]))
    return _row_to_schema(row)


@router.post("/{job_id}/retry", response_model=LaborJobSchema)
async def retry_job(job_id: str, body: JobRetryRequest,
                    principal: Principal = Depends(current_principal)) -> LaborJobSchema:
    row = await _get_job_row(principal, job_id)
    if JobState(row["status"]) not in TERMINAL_STATES:
        raise HTTPException(status.HTTP_409_CONFLICT, "can only retry a terminal job")
    row["status"] = JobState.queued.value
    row["failure_reason"] = None
    row["cancel_requested"] = False
    await wrapper_api.update(wrapper_api.LABOR_JOB, [row])
    await infra.enqueue_job(row["job_id"], token=wrapper_api.get_token())
    return _row_to_schema(row)


@router.post("/{job_id}/fork", response_model=LaborJobSchema)
async def fork_job(job_id: str, body: JobForkRequest,
                   principal: Principal = Depends(current_principal)) -> LaborJobSchema:
    parent = await _get_job_row(principal, job_id)
    new_payload = dict(parent.get("request_payload") or {})
    if body.override_input_data is not None:
        new_payload["input_data"] = body.override_input_data
    forked = orm.LaborJob(
        job_id=orchestrator.new_id("job"),
        tenant_id=parent["tenant_id"],
        workspace_id=parent.get("workspace_id"),
        project_id=parent.get("project_id"),
        cost_center_id=parent.get("cost_center_id"),
        labor_call_id=orchestrator.new_id("lc"),
        status=JobState.queued.value,
        stage=parent.get("stage"),
        request_payload=new_payload,
        trace_context=parent.get("trace_context") or {},
    )
    forked_row = wrapper_api.orm_to_dict(forked)
    await wrapper_api.ingest(wrapper_api.LABOR_JOB, [forked_row])
    await orchestrator.emit_row(forked_row, EventType.job_submitted, status=JobState.queued,
                                message=f"forked from {parent['job_id']}: {body.reason}")
    await infra.enqueue_job(forked_row["job_id"], token=wrapper_api.get_token())
    return _row_to_schema(forked_row)


@router.post("/{job_id}/pause", response_model=LaborJobSchema)
async def pause_job(job_id: str, principal: Principal = Depends(current_principal)) -> LaborJobSchema:
    row = await _get_job_row(principal, job_id)
    if JobState(row["status"]) in TERMINAL_STATES:
        raise HTTPException(status.HTTP_409_CONFLICT, "cannot pause terminal job")
    row["pause_requested"] = True
    if JobState(row["status"]) in {JobState.queued, JobState.scheduled}:
        row["status"] = JobState.paused.value
    await wrapper_api.update(wrapper_api.LABOR_JOB, [row])
    await orchestrator.emit_row(row, EventType.job_paused, status=JobState.paused)
    return _row_to_schema(row)


@router.post("/{job_id}/resume", response_model=LaborJobSchema)
async def resume_job(job_id: str, principal: Principal = Depends(current_principal)) -> LaborJobSchema:
    row = await _get_job_row(principal, job_id)
    if JobState(row["status"]) != JobState.paused:
        raise HTTPException(status.HTTP_409_CONFLICT, "job is not paused")
    row["pause_requested"] = False
    row["status"] = JobState.queued.value
    await wrapper_api.update(wrapper_api.LABOR_JOB, [row])
    await orchestrator.emit_row(row, EventType.job_resumed, status=JobState.queued)
    await infra.enqueue_job(row["job_id"], token=wrapper_api.get_token())
    return _row_to_schema(row)


@router.get("/{job_id}/artifacts")
async def get_artifacts(job_id: str, principal: Principal = Depends(current_principal)) -> dict:
    row = await _get_job_row(principal, job_id)
    refs = wrapper_api.as_rows(
        await wrapper_api.retrieve(wrapper_api.ARTIFACT_REF, {"job_id": row["job_id"]})
    )
    def pick(t): return [_ref_schema(r) for r in refs if r.get("artifact_type") == t]
    return {
        "job_id": job_id,
        "artifacts": [_ref_schema(r) for r in refs],
        "output_refs": pick("parsed_model_output"),
        "validation_result_refs": pick("validation_result"),
        "repair_result_refs": pick("repair_result"),
    }


@router.get("/{job_id}/lineage")
async def get_lineage(job_id: str, principal: Principal = Depends(current_principal)) -> dict:
    row = await _get_job_row(principal, job_id)
    refs = wrapper_api.as_rows(
        await wrapper_api.retrieve(wrapper_api.ARTIFACT_REF, {"job_id": row["job_id"]})
    )
    edges = []
    raws = [r for r in refs if r.get("artifact_type") == "raw_model_output"]
    parsed = [r for r in refs if r.get("artifact_type") == "parsed_model_output"]
    for raw in raws:
        for p in parsed:
            edges.append({"from_artifact_ref_id": raw["artifact_ref_id"],
                          "to_artifact_ref_id": p["artifact_ref_id"], "edge_type": "parsed_from",
                          "job_id": row["job_id"]})
    return {"job_id": job_id, "edges": edges}


@router.get("/{job_id}/ux-state", response_model=LaborJobUxState)
async def ux_state(job_id: str, principal: Principal = Depends(current_principal)) -> LaborJobUxState:
    row = await _get_job_row(principal, job_id)
    st = JobState(row["status"])
    terminal = st in TERMINAL_STATES
    return LaborJobUxState(
        job_id=job_id, status=st, progress_percent=row.get("progress_percent"),
        executor_used=row.get("executor_used"), validation_status=row.get("validation_status"),
        repair_attempt_count=row.get("repair_attempt_count"),
        can_cancel=not terminal, can_retry=terminal,
        can_fork=True, can_approve=(st == JobState.waiting_for_review),
    )


@router.get("/{job_id}/events")
async def stream_events(job_id: str, request: Request,
                        principal: Principal = Depends(current_principal)):
    row = await _get_job_row(principal, job_id)
    after = request.query_params.get("after_event_id", "$")

    async def gen():
        # replay history first if a cursor was given as "0"
        if after == "0":
            for _eid, ev in await infra.read_events(row["job_id"], "0"):
                yield _sse(ev)
        async for ev in infra.tail_events(row["job_id"], last_id="$" if after in ("$", "0") else after):
            if await request.is_disconnected():
                break
            if ev is None:
                yield ": heartbeat\n\n"
                continue
            yield _sse(ev)
            if ev.get("status") in {s.value for s in TERMINAL_STATES}:
                break

    return StreamingResponse(gen(), media_type="text/event-stream")


@router.post("/{job_id}/emit-event", response_model=EmitEventResponse)
async def emit_event(job_id: str, event: JobEvent,
                     principal: Principal = Depends(current_principal)) -> EmitEventResponse:
    row = await _get_job_row(principal, job_id)
    seq = await infra.next_sequence(row["job_id"])
    event_id = orchestrator.new_id("evt")
    ev_row = orm.ExecutionEvent(
        event_id=event_id, event_sequence=seq, tenant_id=row["tenant_id"], job_id=row["job_id"],
        labor_call_id=row.get("labor_call_id"), batch_id=row.get("batch_id"), event_type=event.event_type,
        status=event.status if isinstance(event.status, str) else (event.status.value if event.status else None),
        message=event.message, correlation_id=event.correlation_id, causation_id=event.causation_id,
        parent_event_id=event.parent_event_id, replayable=event.replayable if event.replayable is not None else True,
        trace_id=event.trace_id,
    )
    await wrapper_api.ingest(wrapper_api.EXECUTION_EVENT, [wrapper_api.orm_to_dict(ev_row)])
    await infra.publish_event(row["job_id"], {**event.model_dump(mode="json"), "event_id": event_id, "event_sequence": seq})
    return EmitEventResponse(accepted=True, event_id=event_id, event_sequence=seq)


# ---- helpers --------------------------------------------------------------
def _row_to_schema(row: dict) -> LaborJobSchema:
    return LaborJobSchema(
        job_id=row["job_id"], tenant_id=row["tenant_id"], labor_call_id=row.get("labor_call_id"),
        batch_id=row.get("batch_id"), status=JobState(row["status"]), stage=row.get("stage"),
        executor_used=row.get("executor_used"), progress_percent=row.get("progress_percent"),
        created_at=row.get("created_at"), started_at=row.get("started_at"),
        completed_at=row.get("completed_at"), cancelled_at=row.get("cancelled_at"),
        failure_reason=row.get("failure_reason"), trace_context=row.get("trace_context") or {},
    )


def _ref_schema(r: dict) -> dict:
    return {
        "artifact_ref_id": r["artifact_ref_id"], "artifact_type": r["artifact_type"],
        "storage_system": r.get("storage_system", "GATEWAY"), "version": r.get("version", "1"),
        "tenant_id": r["tenant_id"], "checksum": r.get("checksum"),
        "data_classification": r.get("data_classification"),
    }


def _sse(ev: dict) -> str:
    return f"id: {ev.get('event_sequence','')}\nevent: {ev.get('event_type','message')}\ndata: {json.dumps(ev)}\n\n"
