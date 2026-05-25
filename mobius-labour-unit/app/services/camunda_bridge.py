"""Bridge used by both Camunda invocation paths (external-task worker and REST
service-task endpoint).

run_labor_job_from_payload() creates a labor job from a LaborCallRequest-shaped dict,
runs the EXISTING in-process orchestrator to a terminal state, and returns a compact
result dict suitable for completing a Camunda task (status + output/validation refs).

This is the seam that makes the labor unit ONE coarse Camunda step: the fast inner loop
stays in-process; Camunda only sees submit -> terminal result.

Persistence delegated to the external data wrapper service.
"""
from __future__ import annotations

from typing import Any

from app.core.enums import TERMINAL_STATES, EventType, JobState
from app.models import orm
from app.schemas.labor import LaborCallRequest
from app.services import wrapper_api, orchestrator
from app.services.orchestrator import run_labor_job


async def create_job_from_request(req: LaborCallRequest, *, batch_id: str | None = None) -> str:
    """Persist a queued job from a validated request; return job_id."""
    labor_call_id = req.labor_call_id or orchestrator.new_id("lc")
    job_id = orchestrator.new_id("job")
    job = orm.LaborJob(
        job_id=job_id,
        tenant_id=req.envelope.tenant_id,
        workspace_id=req.envelope.workspace_id,
        project_id=req.envelope.project_id,
        cost_center_id=req.cost_center_id or req.envelope.cost_center_id,
        labor_call_id=labor_call_id,
        batch_id=batch_id,
        status=JobState.queued.value,
        stage=req.stage,
        request_payload=req.model_dump(mode="json"),
        trace_context=req.envelope.trace_context.model_dump(),
    )
    job_row = wrapper_api.orm_to_dict(job)
    await wrapper_api.ingest(wrapper_api.LABOR_JOB, [job_row])
    await orchestrator.emit_row(job_row, EventType.job_submitted, status=JobState.queued)
    return job_id


async def run_to_terminal(job_id: str) -> dict[str, Any]:
    """Run an existing queued job to a terminal state; return a Camunda-friendly result."""
    job = await wrapper_api.retrieve_one(wrapper_api.LABOR_JOB, {"job_id": job_id})
    if job is None:
        return {"job_id": job_id, "status": "failed", "error": "job not found"}
    if JobState(job["status"]) not in TERMINAL_STATES:
        job = await run_labor_job(job)
    refs = wrapper_api.as_rows(
        await wrapper_api.retrieve(wrapper_api.ARTIFACT_REF, {"job_id": job_id})
    )
    parsed = next((r["artifact_ref_id"] for r in refs if r.get("artifact_type") == "parsed_model_output"), None)
    validation = next((r["artifact_ref_id"] for r in refs if r.get("artifact_type") == "validation_result"), None)
    return {
        "job_id": job_id,
        "labor_call_id": job.get("labor_call_id"),
        "status": job["status"],
        "succeeded": JobState(job["status"]) == JobState.succeeded,
        "executor_used": job.get("executor_used"),
        "validation_status": job.get("validation_status"),
        "parsed_output_ref": parsed,
        "validation_result_ref": validation,
        "failure_reason": job.get("failure_reason"),
    }


async def run_labor_job_from_payload(payload: dict[str, Any], *, batch_id: str | None = None) -> dict[str, Any]:
    """Full coarse path: parse -> create -> run -> return terminal result."""
    req = LaborCallRequest.model_validate(payload)
    job_id = await create_job_from_request(req, batch_id=batch_id)
    return await run_to_terminal(job_id)
