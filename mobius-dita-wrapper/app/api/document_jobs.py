"""Async document-job endpoints."""
from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException, Request, Response, status
from fastapi.responses import StreamingResponse
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.api._sse import sse_event_stream
from app.core.auth import Principal, current_principal, enforce_tenant
from app.core.enums import TERMINAL_STATES, EventType, JobState
from app.db.session import get_session
from app.models import orm
from app.schemas.dita import DocumentJob, DocumentJobArtifacts, DocumentJobRequest
from app.services import common, infra

router = APIRouter(prefix="/document-jobs", tags=["Document Jobs"])
SEC = [{"bearerAuth": []}]


async def _load(session, job_id, principal) -> orm.DocumentJob:
    job = await session.get(orm.DocumentJob, job_id)
    if not job:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "document job not found")
    enforce_tenant(principal, job.tenant_id)
    return job


@router.post("", response_model=DocumentJob)
async def submit_document_job(req: DocumentJobRequest, request: Request, response: Response,
                              principal: Principal = Depends(current_principal),
                              session: AsyncSession = Depends(get_session)) -> DocumentJob:
    enforce_tenant(principal, req.envelope.tenant_id)
    existing = await common.find_idempotent(session, req.envelope.tenant_id, "document.job", req.idempotency_key)
    if existing:
        response.status_code = status.HTTP_409_CONFLICT
        return _to_schema(await session.get(orm.DocumentJob, existing))

    job_id = common.new_id("djob")
    payload = req.model_dump(mode="json")
    auth = request.headers.get("authorization")
    if auth:
        # Forwarded to existing render service / labor gateway by the worker.
        # NOTE: stored in DB; rotate render-service token frequently.
        payload["__auth_header"] = auth
    job = orm.DocumentJob(
        job_id=job_id, tenant_id=req.envelope.tenant_id, workspace_id=req.envelope.workspace_id,
        project_id=req.envelope.project_id, submitted_by=req.envelope.initiator_id,
        job_type=req.job_type if isinstance(req.job_type, str) else req.job_type.value,
        status=JobState.queued.value, request_payload=payload,
        trace_context=req.envelope.trace_context.model_dump(),
    )
    session.add(job)
    await common.record_idempotent(session, req.envelope.tenant_id, "document.job", req.idempotency_key, job_id)
    await session.flush()
    await common.emit(session, job_id=job_id, tenant_id=job.tenant_id, trace_context=job.trace_context,
                      event_type=EventType.job_submitted, status=JobState.queued)
    await session.commit()
    await infra.enqueue_job(job_id)
    out = _to_schema(job)
    out.events_url = f"/document-jobs/{job_id}/events"
    out.artifacts_url = f"/document-jobs/{job_id}/artifacts"
    out.lineage_url = f"/document-jobs/{job_id}/lineage"
    out.cancel_url = f"/document-jobs/{job_id}/cancel"
    return out


@router.get("/{job_id}", response_model=DocumentJob)
async def get_document_job(job_id: str, principal: Principal = Depends(current_principal),
                           session: AsyncSession = Depends(get_session)) -> DocumentJob:
    return _to_schema(await _load(session, job_id, principal))


@router.post("/{job_id}/cancel", response_model=DocumentJob)
async def cancel_document_job(job_id: str, principal: Principal = Depends(current_principal),
                              session: AsyncSession = Depends(get_session)) -> DocumentJob:
    job = await _load(session, job_id, principal)
    if JobState(job.status) in TERMINAL_STATES:
        raise HTTPException(status.HTTP_409_CONFLICT, "job is terminal")
    job.cancel_requested = True
    if JobState(job.status) in {JobState.queued, JobState.scheduled, JobState.paused}:
        job.status = JobState.cancelled.value
    return _to_schema(job)


@router.post("/{job_id}/retry", response_model=DocumentJob)
async def retry_document_job(job_id: str, principal: Principal = Depends(current_principal),
                             session: AsyncSession = Depends(get_session)) -> DocumentJob:
    job = await _load(session, job_id, principal)
    if JobState(job.status) not in TERMINAL_STATES:
        raise HTTPException(status.HTTP_409_CONFLICT, "can only retry a terminal job")
    job.status = JobState.queued.value; job.failure_reason = None; job.cancel_requested = False
    await session.commit()
    await infra.enqueue_job(job_id)
    return _to_schema(job)


@router.post("/{job_id}/pause", response_model=DocumentJob)
async def pause_document_job(job_id: str, principal: Principal = Depends(current_principal),
                             session: AsyncSession = Depends(get_session)) -> DocumentJob:
    job = await _load(session, job_id, principal)
    if JobState(job.status) in TERMINAL_STATES:
        raise HTTPException(status.HTTP_409_CONFLICT, "job is terminal")
    job.pause_requested = True
    if JobState(job.status) in {JobState.queued, JobState.scheduled}:
        job.status = JobState.paused.value
    await common.emit(session, job_id=job_id, tenant_id=job.tenant_id, trace_context=job.trace_context,
                      event_type=EventType.job_paused, status=JobState.paused)
    return _to_schema(job)


@router.post("/{job_id}/resume", response_model=DocumentJob)
async def resume_document_job(job_id: str, principal: Principal = Depends(current_principal),
                              session: AsyncSession = Depends(get_session)) -> DocumentJob:
    job = await _load(session, job_id, principal)
    if JobState(job.status) != JobState.paused:
        raise HTTPException(status.HTTP_409_CONFLICT, "job is not paused")
    job.pause_requested = False; job.status = JobState.queued.value
    await common.emit(session, job_id=job_id, tenant_id=job.tenant_id, trace_context=job.trace_context,
                      event_type=EventType.job_resumed, status=JobState.queued)
    await session.commit()
    await infra.enqueue_job(job_id)
    return _to_schema(job)


@router.get("/{job_id}/artifacts", response_model=DocumentJobArtifacts)
async def document_job_artifacts(job_id: str, principal: Principal = Depends(current_principal),
                                 session: AsyncSession = Depends(get_session)) -> DocumentJobArtifacts:
    job = await _load(session, job_id, principal)
    refs = (await session.execute(select(orm.ArtifactRef).where(orm.ArtifactRef.job_id == job_id))).scalars().all()
    def pick(t): return [common.artifact_dict(r) for r in refs if r.artifact_type == t]
    return DocumentJobArtifacts(
        job_id=job_id, artifacts=[common.artifact_dict(r) for r in refs],
        rendered_outputs=pick("rendered_document"), generated_documents=pick("generated_document"),
        validation_results=pick("validation_result"), repair_results=pick("repair_result"),
    )


@router.get("/{job_id}/lineage")
async def document_job_lineage(job_id: str, principal: Principal = Depends(current_principal),
                               session: AsyncSession = Depends(get_session)) -> dict:
    await _load(session, job_id, principal)
    edges = (await session.execute(select(orm.LineageEdge).where(orm.LineageEdge.job_id == job_id))).scalars().all()
    return {"job_id": job_id, "edges": [{"from": e.from_artifact_ref, "to": e.to_artifact_ref,
                                         "edge_type": e.edge_type} for e in edges]}


@router.get("/{job_id}/events")
async def document_job_events(job_id: str, principal: Principal = Depends(current_principal),
                              session: AsyncSession = Depends(get_session)):
    await _load(session, job_id, principal)
    return StreamingResponse(sse_event_stream(job_id), media_type="text/event-stream")


def _to_schema(job: orm.DocumentJob) -> DocumentJob:
    return DocumentJob(
        job_id=job.job_id, status=JobState(job.status), tenant_id=job.tenant_id,
        workspace_id=job.workspace_id, project_id=job.project_id, submitted_by=job.submitted_by,
        document_workflow_id=job.document_workflow_id, prompt_chain_run_id=job.prompt_chain_run_id,
        current_step_id=job.current_step_id, progress_percent=job.progress_percent,
        created_at=job.created_at, trace_context=job.trace_context,
    )
