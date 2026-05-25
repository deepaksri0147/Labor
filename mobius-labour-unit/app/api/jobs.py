"""Labor job lifecycle: get/cancel/retry/fork/pause/resume/artifacts/lineage/ux-state/
events (SSE)/emit-event."""
from __future__ import annotations

import asyncio
import json

from fastapi import APIRouter, Depends, HTTPException, Request, status
from fastapi.responses import StreamingResponse
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.auth import Principal, current_principal, enforce_tenant
from app.core.enums import TERMINAL_STATES, EventType, JobState
from app.db.session import SessionLocal, get_session
from app.models import orm
from app.schemas.labor import (
    ArtifactRef as ArtifactRefSchema,
    EmitEventResponse,
    JobCancelRequest,
    JobEvent,
    JobForkRequest,
    JobRetryRequest,
    LaborJob as LaborJobSchema,
    LaborJobUxState,
)
from app.services import infra, orchestrator

router = APIRouter(prefix="/labor/jobs", tags=["Labor Jobs"])


async def _get_job(session: AsyncSession, principal: Principal, job_id: str) -> orm.LaborJob:
    job = await session.get(orm.LaborJob, job_id)
    if not job:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "job not found")
    enforce_tenant(principal, job.tenant_id)
    return job


@router.get("/{job_id}", response_model=LaborJobSchema)
async def get_job(job_id: str, principal: Principal = Depends(current_principal),
                  session: AsyncSession = Depends(get_session)) -> LaborJobSchema:
    job = await _get_job(session, principal, job_id)
    return _to_schema(job)


@router.post("/{job_id}/cancel", response_model=LaborJobSchema)
async def cancel_job(job_id: str, body: JobCancelRequest,
                     principal: Principal = Depends(current_principal),
                     session: AsyncSession = Depends(get_session)) -> LaborJobSchema:
    job = await _get_job(session, principal, job_id)
    if JobState(job.status) in TERMINAL_STATES:
        raise HTTPException(status.HTTP_409_CONFLICT, f"cannot cancel job in terminal state {job.status}")
    job.cancel_requested = True
    if JobState(job.status) in {JobState.queued, JobState.scheduled, JobState.paused}:
        # not yet running: cancel immediately
        await orchestrator._finish(session, job, JobState.cancelled, body.reason)
    else:
        await orchestrator.emit(session, job, EventType.job_cancel_requested, status=JobState(job.status))
    return _to_schema(job)


@router.post("/{job_id}/retry", response_model=LaborJobSchema)
async def retry_job(job_id: str, body: JobRetryRequest,
                    principal: Principal = Depends(current_principal),
                    session: AsyncSession = Depends(get_session)) -> LaborJobSchema:
    job = await _get_job(session, principal, job_id)
    if JobState(job.status) not in TERMINAL_STATES:
        raise HTTPException(status.HTTP_409_CONFLICT, "can only retry a terminal job")
    job.status = JobState.queued.value
    job.failure_reason = None
    job.cancel_requested = False
    await session.commit()
    await infra.enqueue_job(job.job_id)
    return _to_schema(job)


@router.post("/{job_id}/fork", response_model=LaborJobSchema)
async def fork_job(job_id: str, body: JobForkRequest,
                   principal: Principal = Depends(current_principal),
                   session: AsyncSession = Depends(get_session)) -> LaborJobSchema:
    parent = await _get_job(session, principal, job_id)
    new_payload = dict(parent.request_payload)
    if body.override_input_data is not None:
        new_payload["input_data"] = body.override_input_data
    forked = orm.LaborJob(
        job_id=orchestrator.new_id("job"),
        tenant_id=parent.tenant_id,
        workspace_id=parent.workspace_id,
        project_id=parent.project_id,
        cost_center_id=parent.cost_center_id,
        labor_call_id=orchestrator.new_id("lc"),
        status=JobState.queued.value,
        stage=parent.stage,
        request_payload=new_payload,
        trace_context=parent.trace_context,
    )
    session.add(forked)
    await session.flush()
    # lineage: forked_from
    await orchestrator.emit(session, forked, EventType.job_submitted, status=JobState.queued,
                            message=f"forked from {parent.job_id}: {body.reason}")
    await session.commit()
    await infra.enqueue_job(forked.job_id)
    return _to_schema(forked)


@router.post("/{job_id}/pause", response_model=LaborJobSchema)
async def pause_job(job_id: str, principal: Principal = Depends(current_principal),
                    session: AsyncSession = Depends(get_session)) -> LaborJobSchema:
    job = await _get_job(session, principal, job_id)
    if JobState(job.status) in TERMINAL_STATES:
        raise HTTPException(status.HTTP_409_CONFLICT, "cannot pause terminal job")
    job.pause_requested = True
    if JobState(job.status) in {JobState.queued, JobState.scheduled}:
        job.status = JobState.paused.value
    await orchestrator.emit(session, job, EventType.job_paused, status=JobState.paused)
    return _to_schema(job)


@router.post("/{job_id}/resume", response_model=LaborJobSchema)
async def resume_job(job_id: str, principal: Principal = Depends(current_principal),
                     session: AsyncSession = Depends(get_session)) -> LaborJobSchema:
    job = await _get_job(session, principal, job_id)
    if JobState(job.status) != JobState.paused:
        raise HTTPException(status.HTTP_409_CONFLICT, "job is not paused")
    job.pause_requested = False
    job.status = JobState.queued.value
    await orchestrator.emit(session, job, EventType.job_resumed, status=JobState.queued)
    await session.commit()
    await infra.enqueue_job(job.job_id)
    return _to_schema(job)


@router.get("/{job_id}/artifacts")
async def get_artifacts(job_id: str, principal: Principal = Depends(current_principal),
                        session: AsyncSession = Depends(get_session)) -> dict:
    job = await _get_job(session, principal, job_id)
    refs = (await session.execute(
        select(orm.ArtifactRef).where(orm.ArtifactRef.job_id == job.job_id)
    )).scalars().all()
    def pick(t): return [_ref_schema(r) for r in refs if r.artifact_type == t]
    return {
        "job_id": job_id,
        "artifacts": [_ref_schema(r) for r in refs],
        "output_refs": pick("parsed_model_output"),
        "validation_result_refs": pick("validation_result"),
        "repair_result_refs": pick("repair_result"),
    }


@router.get("/{job_id}/lineage")
async def get_lineage(job_id: str, principal: Principal = Depends(current_principal),
                      session: AsyncSession = Depends(get_session)) -> dict:
    job = await _get_job(session, principal, job_id)
    refs = (await session.execute(
        select(orm.ArtifactRef).where(orm.ArtifactRef.job_id == job.job_id)
    )).scalars().all()
    edges = []
    raws = [r for r in refs if r.artifact_type == "raw_model_output"]
    parsed = [r for r in refs if r.artifact_type == "parsed_model_output"]
    for raw in raws:
        for p in parsed:
            edges.append({"from_artifact_ref_id": raw.artifact_ref_id,
                          "to_artifact_ref_id": p.artifact_ref_id, "edge_type": "parsed_from",
                          "job_id": job.job_id})
    return {"job_id": job_id, "edges": edges}


@router.get("/{job_id}/ux-state", response_model=LaborJobUxState)
async def ux_state(job_id: str, principal: Principal = Depends(current_principal),
                   session: AsyncSession = Depends(get_session)) -> LaborJobUxState:
    job = await _get_job(session, principal, job_id)
    st = JobState(job.status)
    terminal = st in TERMINAL_STATES
    return LaborJobUxState(
        job_id=job_id, status=st, progress_percent=job.progress_percent,
        executor_used=job.executor_used, validation_status=job.validation_status,
        repair_attempt_count=job.repair_attempt_count,
        can_cancel=not terminal, can_retry=terminal,
        can_fork=True, can_approve=(st == JobState.waiting_for_review),
    )


@router.get("/{job_id}/events")
async def stream_events(job_id: str, request: Request,
                        principal: Principal = Depends(current_principal),
                        session: AsyncSession = Depends(get_session)):
    job = await _get_job(session, principal, job_id)
    after = request.query_params.get("after_event_id", "$")

    async def gen():
        # replay history first if a cursor was given as "0"
        if after == "0":
            for _eid, ev in await infra.read_events(job.job_id, "0"):
                yield _sse(ev)
        async for ev in infra.tail_events(job.job_id, last_id="$" if after in ("$", "0") else after):
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
                     principal: Principal = Depends(current_principal),
                     session: AsyncSession = Depends(get_session)) -> EmitEventResponse:
    job = await _get_job(session, principal, job_id)
    seq = await infra.next_sequence(job.job_id)
    event_id = orchestrator.new_id("evt")
    row = orm.ExecutionEvent(
        event_id=event_id, event_sequence=seq, tenant_id=job.tenant_id, job_id=job.job_id,
        labor_call_id=job.labor_call_id, batch_id=job.batch_id, event_type=event.event_type,
        status=event.status if isinstance(event.status, str) else (event.status.value if event.status else None),
        message=event.message, correlation_id=event.correlation_id, causation_id=event.causation_id,
        parent_event_id=event.parent_event_id, replayable=event.replayable if event.replayable is not None else True,
        trace_id=event.trace_id,
    )
    session.add(row)
    await session.flush()
    await infra.publish_event(job.job_id, {**event.model_dump(mode="json"), "event_id": event_id, "event_sequence": seq})
    return EmitEventResponse(accepted=True, event_id=event_id, event_sequence=seq)


# ---- helpers --------------------------------------------------------------
def _to_schema(job: orm.LaborJob) -> LaborJobSchema:
    return LaborJobSchema(
        job_id=job.job_id, tenant_id=job.tenant_id, labor_call_id=job.labor_call_id,
        batch_id=job.batch_id, status=JobState(job.status), stage=job.stage,
        executor_used=job.executor_used, progress_percent=job.progress_percent,
        created_at=job.created_at, started_at=job.started_at, completed_at=job.completed_at,
        cancelled_at=job.cancelled_at, failure_reason=job.failure_reason,
        trace_context=job.trace_context,
    )


def _ref_schema(r: orm.ArtifactRef) -> dict:
    return {
        "artifact_ref_id": r.artifact_ref_id, "artifact_type": r.artifact_type,
        "storage_system": r.storage_system, "version": r.version, "tenant_id": r.tenant_id,
        "checksum": r.checksum, "data_classification": r.data_classification,
    }


def _sse(ev: dict) -> str:
    return f"id: {ev.get('event_sequence','')}\nevent: {ev.get('event_type','message')}\ndata: {json.dumps(ev)}\n\n"
