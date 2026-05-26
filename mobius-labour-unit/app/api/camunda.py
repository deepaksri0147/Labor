"""Camunda/Bob integration endpoints (the REST service-task path + workflow ops).

These complement the external-task worker. A workflow can either:
  - have the gateway's worker CLAIM a labor.execute task (pull), or
  - call POST /camunda/labor/run-sync here as a service task (push),
whichever the BPMN is authored for. Both run the same in-process orchestrator.
"""
from __future__ import annotations

from fastapi import APIRouter, BackgroundTasks, Depends, HTTPException, status

from app.core.auth import Principal, current_principal, enforce_tenant
from app.schemas.labor import LaborCallRequest
from app.services import wrapper_api, infra
from app.services.bob_camunda import BobCamundaClient, map_process_state_to_jobstate
from app.services.camunda_bridge import create_job_from_request, run_to_terminal

router = APIRouter(prefix="/camunda", tags=["Camunda Integration"])


@router.post("/labor/run-sync")
async def run_labor_sync(
    req: LaborCallRequest,
    principal: Principal = Depends(current_principal),
) -> dict:
    """Service-task push path: run a labor job to terminal and return the result inline.

    Use for short jobs Camunda waits on. For long jobs use run-async + the worker, or the
    external-task pull path.
    """
    enforce_tenant(principal, req.envelope.tenant_id)
    job_id = await create_job_from_request(req)
    return await run_to_terminal(job_id)


@router.post("/labor/run-async")
async def run_labor_async(
    req: LaborCallRequest,
    principal: Principal = Depends(current_principal),
) -> dict:
    """Start a labor job and return its id; the Redis worker drains it. Camunda polls
    /labor/jobs/{id} or subscribes to events for completion."""
    enforce_tenant(principal, req.envelope.tenant_id)
    job_id = await create_job_from_request(req)
    await infra.enqueue_job(job_id, token=wrapper_api.get_token())
    return {"job_id": job_id, "status": "queued"}


@router.post("/workflows/publish")
async def publish_workflow(
    dto: dict,
    principal: Principal = Depends(current_principal),
) -> dict:
    """Publish/import a labor workflow definition (WorkflowPostDto) into Bob so the
    pipeline is re-stitched in the engine rather than hardcoded."""
    client = BobCamundaClient()
    if dto.get("import"):
        return await client.import_workflow(dto)
    return await client.create_workflow(dto)


@router.get("/labor/jobs/{job_id}/reconcile")
async def reconcile_status(
    job_id: str,
    pipeline_id: str,
    principal: Principal = Depends(current_principal),
) -> dict:
    """Reconcile the gateway's job status against Bob's process status.

    The outer process state in Bob/Camunda is the source of truth for the WORKFLOW; the
    gateway's labor_job.status is the source of truth for the inner labor RUN. This
    surfaces both and projects Bob's state onto our vocabulary when it is more advanced
    (e.g. the workflow was cancelled at the engine level)."""
    job = await wrapper_api.retrieve_one(wrapper_api.LABOR_JOB, {"job_id": job_id})
    if not job:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "job not found")
    enforce_tenant(principal, job["tenant_id"])
    client = BobCamundaClient()
    try:
        process = await client.pipeline_status(pipeline_id)
    except Exception as e:  # Bob unreachable -> report gateway-local view only
        return {"job_id": job_id, "gateway_status": job["status"], "process_status": None,
                "note": f"bob status unavailable: {e}"}
    projected = map_process_state_to_jobstate(process)
    return {
        "job_id": job_id,
        "gateway_status": job["status"],
        "process_status": process.get("state") or process.get("status"),
        "projected_jobstate": projected,
    }
