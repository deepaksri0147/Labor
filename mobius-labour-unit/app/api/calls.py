"""Labor call endpoints: submit, get, validate, repair, revalidate.

Persistence is delegated to the external data wrapper service (ingest/retrieve/update).
"""
from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException, Response, status

from app.core.auth import Principal, current_principal, enforce_tenant
from app.core.enums import JobState
from app.models import orm
from app.schemas.labor import (
    LaborCallRequest,
    LaborCallResponse,
    RepairRequest,
    ValidationRequest,
    ValidationResult,
    Error,
)
from app.services import wrapper_api, infra, orchestrator
from app.services.validation import validate_against_schema

router = APIRouter(prefix="/labor", tags=["Labor"])
ENDPOINT = "labor.calls"


@router.post("/calls", response_model=LaborCallResponse)
async def submit_labor_call(
    req: LaborCallRequest,
    response: Response,
    principal: Principal = Depends(current_principal),
) -> LaborCallResponse:
    enforce_tenant(principal, req.envelope.tenant_id)

    # idempotency: a replayed key returns the original result, never a new run
    existing = await orchestrator.find_idempotent(req.envelope.tenant_id, ENDPOINT, req.idempotency_key)
    if existing:
        response.status_code = status.HTTP_409_CONFLICT
        job_row = await wrapper_api.retrieve_one(wrapper_api.LABOR_JOB, {"job_id": existing})
        if not job_row:
            raise HTTPException(status.HTTP_404_NOT_FOUND, "idempotent job not found")
        artifacts = wrapper_api.as_rows(
            await wrapper_api.retrieve(wrapper_api.ARTIFACT_REF, {"job_id": existing})
        )
        return _row_to_call_response(job_row, artifacts)

    labor_call_id = req.labor_call_id or orchestrator.new_id("lc")
    job_id = orchestrator.new_id("job")
    job = orm.LaborJob(
        job_id=job_id,
        tenant_id=req.envelope.tenant_id,
        workspace_id=req.envelope.workspace_id,
        project_id=req.envelope.project_id,
        cost_center_id=req.cost_center_id or req.envelope.cost_center_id,
        labor_call_id=labor_call_id,
        status=JobState.queued.value,
        stage=req.stage,
        request_payload=req.model_dump(mode="json"),
        trace_context=req.envelope.trace_context.model_dump(),
    )
    await wrapper_api.ingest(wrapper_api.LABOR_JOB, [wrapper_api.orm_to_dict(job)])
    await orchestrator.record_idempotent(req.envelope.tenant_id, ENDPOINT, req.idempotency_key, job_id)
    await orchestrator.emit(job, orchestrator.EventType.job_submitted, status=JobState.queued)
    await infra.enqueue_job(job_id, token=wrapper_api.get_token())

    return LaborCallResponse(labor_call_id=labor_call_id, job_id=job_id, status=JobState.queued, stage=req.stage)


@router.get("/calls/{labor_call_id}", response_model=LaborCallResponse)
async def get_labor_call(
    labor_call_id: str,
    principal: Principal = Depends(current_principal),
) -> LaborCallResponse:
    job_row = await wrapper_api.retrieve_one(wrapper_api.LABOR_JOB, {"labor_call_id": labor_call_id})
    if not job_row:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "labor call not found")
    enforce_tenant(principal, job_row["tenant_id"])
    artifacts = wrapper_api.as_rows(
        await wrapper_api.retrieve(wrapper_api.ARTIFACT_REF, {"job_id": job_row["job_id"]})
    )
    return _row_to_call_response(job_row, artifacts)


@router.post("/calls/{labor_call_id}/validate", response_model=ValidationResult)
async def validate_output(
    labor_call_id: str,
    req: ValidationRequest,
    principal: Principal = Depends(current_principal),
) -> ValidationResult:
    parsed = await _load_artifact_content(principal, req.parsed_output_ref.artifact_ref_id)
    schema = await _load_artifact_content(principal, req.validation_schema_ref.artifact_ref_id)
    errors = validate_against_schema(parsed, schema) if isinstance(schema, dict) else []
    return ValidationResult(
        validation_result_id=orchestrator.new_id("vr"),
        status="passed" if not errors else "failed",
        errors=errors or None,
        repair_required=bool(errors),
    )


@router.post("/calls/{labor_call_id}/repair", response_model=LaborCallResponse)
async def repair_output(
    labor_call_id: str,
    req: RepairRequest,
    principal: Principal = Depends(current_principal),
) -> LaborCallResponse:
    job_row = await wrapper_api.retrieve_one(wrapper_api.LABOR_JOB, {"labor_call_id": labor_call_id})
    if not job_row:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "labor call not found")
    enforce_tenant(principal, job_row["tenant_id"])
    # re-enqueue with a repair flag in the payload; the worker runs the repair loop
    job_row["request_payload"] = {
        **(job_row.get("request_payload") or {}),
        "_force_repair": True,
        "validation_required": True,
    }
    job_row["status"] = JobState.queued.value
    await wrapper_api.update(wrapper_api.LABOR_JOB, [job_row])
    await infra.enqueue_job(job_row["job_id"], token=wrapper_api.get_token())
    artifacts = wrapper_api.as_rows(
        await wrapper_api.retrieve(wrapper_api.ARTIFACT_REF, {"job_id": job_row["job_id"]})
    )
    return _row_to_call_response(job_row, artifacts)


@router.post("/calls/{labor_call_id}/revalidate", response_model=ValidationResult)
async def revalidate_output(
    labor_call_id: str,
    req: ValidationRequest,
    principal: Principal = Depends(current_principal),
) -> ValidationResult:
    return await validate_output(labor_call_id, req, principal)


# ---- helpers --------------------------------------------------------------
def _row_to_call_response(job: dict, artifacts: list[dict]) -> LaborCallResponse:
    raw_ref = next((a for a in artifacts if a.get("artifact_type") == "raw_model_output"), None)
    parsed_ref = next((a for a in artifacts if a.get("artifact_type") == "parsed_model_output"), None)
    val_ref = next((a for a in artifacts if a.get("artifact_type") == "validation_result"), None)

    def _to_schema_ref(row: dict | None):
        if not row:
            return None
        from app.schemas.labor import ArtifactRef
        return ArtifactRef(
            artifact_ref_id=row["artifact_ref_id"],
            artifact_type=row["artifact_type"],
            storage_system=row.get("storage_system", "GATEWAY"),
            version=row.get("version", "1"),
            tenant_id=row["tenant_id"],
            uri=row.get("uri"),
            checksum=row.get("checksum"),
            data_classification=row.get("data_classification"),
            created_at=row.get("created_at"),
            content=row.get("content"),
        )

    return LaborCallResponse(
        labor_call_id=job.get("labor_call_id") or "",
        job_id=job["job_id"],
        status=JobState(job["status"]),
        stage=job.get("stage"),
        executor_used=job.get("executor_used"),
        started_at=job.get("started_at"),
        completed_at=job.get("completed_at"),
        raw_output_ref=_to_schema_ref(raw_ref),
        parsed_output_ref=_to_schema_ref(parsed_ref),
        validation_result_ref=_to_schema_ref(val_ref),
        error=Error(error_code="execution_failed", message=job["failure_reason"]) if job.get("failure_reason") else None,
        trace_context=job.get("trace_context") or None,
    )


async def _load_artifact_content(principal: Principal, artifact_ref_id: str):
    ref = await wrapper_api.retrieve_one(wrapper_api.ARTIFACT_REF, {"artifact_ref_id": artifact_ref_id})
    if not ref:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "artifact not found")
    enforce_tenant(principal, ref["tenant_id"])
    content = ref.get("content") or {}
    return content.get("value", content)
