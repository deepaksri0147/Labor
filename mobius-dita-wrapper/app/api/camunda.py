"""Bob/Camunda integration for DITA (coarse-grained, same model as the labor gateway).

A document job or chain run is ONE coarse step in a Bob/Camunda workflow. The fast inner
work (render, chain steps, labor calls) stays in-process; Camunda orchestrates across
services. Both invocation styles supported: external-task pull (worker) and REST push.
"""
from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException, Request, status
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.auth import Principal, current_principal, enforce_tenant
from app.db.session import get_session
from app.models import orm
from app.schemas.dita import DocumentJobRequest
from app.services import common, infra
from app.services.clients import bob_client

router = APIRouter(prefix="/camunda", tags=["Camunda Integration"])
SEC = [{"bearerAuth": []}]


@router.post("/document-jobs/run-async")
async def run_document_job_async(req: DocumentJobRequest, request: Request,
                                 principal: Principal = Depends(current_principal),
                                 session: AsyncSession = Depends(get_session)) -> dict:
    """Service-task push: start a document job; the worker drains it; Camunda polls status."""
    enforce_tenant(principal, req.envelope.tenant_id)
    from app.core.enums import EventType, JobState
    job_id = common.new_id("djob")
    payload = req.model_dump(mode="json")
    auth = request.headers.get("authorization")
    if auth:
        payload["__auth_header"] = auth
    job = orm.DocumentJob(
        job_id=job_id, tenant_id=req.envelope.tenant_id, workspace_id=req.envelope.workspace_id,
        project_id=req.envelope.project_id, submitted_by=req.envelope.initiator_id,
        job_type=req.job_type if isinstance(req.job_type, str) else req.job_type.value,
        status=JobState.queued.value, request_payload=payload,
        trace_context=req.envelope.trace_context.model_dump(),
    )
    session.add(job)
    await session.flush()
    await common.emit(session, job_id=job_id, tenant_id=job.tenant_id, trace_context=job.trace_context,
                      event_type=EventType.job_submitted, status=JobState.queued)
    await session.commit()
    await infra.enqueue_job(job_id)
    return {"job_id": job_id, "status": "queued"}


@router.post("/workflows/publish")
async def publish_workflow(dto: dict, principal: Principal = Depends(current_principal)) -> dict:
    """Import/create a DITA document workflow definition in Bob (re-stitch path)."""
    return await bob_client.import_workflow(dto)


@router.get("/document-jobs/{job_id}/reconcile")
async def reconcile(job_id: str, pipeline_id: str, principal: Principal = Depends(current_principal),
                    session: AsyncSession = Depends(get_session)) -> dict:
    job = await session.get(orm.DocumentJob, job_id)
    if not job:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "job not found")
    enforce_tenant(principal, job.tenant_id)
    try:
        process = await bob_client.pipeline_status(pipeline_id)
    except Exception as e:
        return {"job_id": job_id, "gateway_status": job.status, "process_status": None, "note": f"bob unavailable: {e}"}
    return {"job_id": job_id, "gateway_status": job.status,
            "process_status": process.get("state") or process.get("status")}
