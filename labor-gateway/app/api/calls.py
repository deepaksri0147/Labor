"""Labor call endpoints: submit, get, validate, repair, revalidate."""
from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException, Response, status
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.auth import Principal, current_principal, enforce_tenant
from app.core.enums import JobState
from app.db.session import get_session
from sqlalchemy.orm import selectinload
from app.models import orm
from app.schemas.labor import (
    LaborCallRequest,
    LaborCallResponse,
    RepairRequest,
    ValidationRequest,
    ValidationResult,
)
from app.services import infra, orchestrator
from app.services.validation import validate_against_schema

router = APIRouter(prefix="/labor", tags=["Labor"])
ENDPOINT = "labor.calls"


@router.post("/calls", response_model=LaborCallResponse)
async def submit_labor_call(
    req: LaborCallRequest,
    response: Response,
    principal: Principal = Depends(current_principal),
    session: AsyncSession = Depends(get_session),
) -> LaborCallResponse:
    enforce_tenant(principal, req.envelope.tenant_id)

    # idempotency: a replayed key returns the original result, never a new run
    existing = await orchestrator.find_idempotent(session, req.envelope.tenant_id, ENDPOINT, req.idempotency_key)
    if existing:
        response.status_code = status.HTTP_409_CONFLICT
        job = await session.get(orm.LaborJob, existing, options=[selectinload(orm.LaborJob.artifacts)])
        return _job_to_call_response(job)

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
    session.add(job)
    await orchestrator.record_idempotent(session, req.envelope.tenant_id, ENDPOINT, req.idempotency_key, job_id)
    await session.flush()
    await orchestrator.emit(session, job, orchestrator.EventType.job_submitted, status=JobState.queued)
    await session.commit()
    await infra.enqueue_job(job_id)

    return LaborCallResponse(labor_call_id=labor_call_id, job_id=job_id, status=JobState.queued, stage=req.stage)


@router.get("/calls/{labor_call_id}", response_model=LaborCallResponse)
async def get_labor_call(
    labor_call_id: str,
    principal: Principal = Depends(current_principal),
    session: AsyncSession = Depends(get_session),
) -> LaborCallResponse:
    job = (await session.execute(
        select(orm.LaborJob).options(selectinload(orm.LaborJob.artifacts)).where(orm.LaborJob.labor_call_id == labor_call_id)
    )).scalar_one_or_none()
    if not job:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "labor call not found")
    enforce_tenant(principal, job.tenant_id)
    return _job_to_call_response(job)


@router.post("/calls/{labor_call_id}/validate", response_model=ValidationResult)
async def validate_output(
    labor_call_id: str,
    req: ValidationRequest,
    principal: Principal = Depends(current_principal),
    session: AsyncSession = Depends(get_session),
) -> ValidationResult:
    parsed = await _load_artifact_content(session, principal, req.parsed_output_ref.artifact_ref_id)
    schema = await _load_artifact_content(session, principal, req.validation_schema_ref.artifact_ref_id)
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
    session: AsyncSession = Depends(get_session),
) -> LaborCallResponse:
    job = (await session.execute(
        select(orm.LaborJob).where(orm.LaborJob.labor_call_id == labor_call_id)
    )).scalar_one_or_none()
    if not job:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "labor call not found")
    enforce_tenant(principal, job.tenant_id)
    # re-enqueue with a repair flag in the payload; the worker runs the repair loop
    job.request_payload = {**job.request_payload, "_force_repair": True,
                           "validation_required": True}
    job.status = JobState.queued.value
    await session.commit()
    await infra.enqueue_job(job.job_id)
    return _job_to_call_response(job)


@router.post("/calls/{labor_call_id}/revalidate", response_model=ValidationResult)
async def revalidate_output(
    labor_call_id: str,
    req: ValidationRequest,
    principal: Principal = Depends(current_principal),
    session: AsyncSession = Depends(get_session),
) -> ValidationResult:
    return await validate_output(labor_call_id, req, principal, session)


# ---- helpers --------------------------------------------------------------
def _job_to_call_response(job: orm.LaborJob) -> LaborCallResponse:
    raw_ref = next((a for a in job.artifacts if a.artifact_type == "raw_model_output"), None)
    parsed_ref = next((a for a in job.artifacts if a.artifact_type == "parsed_model_output"), None)
    val_ref = next((a for a in job.artifacts if a.artifact_type == "validation_result"), None)
    
    def _to_schema_ref(orm_ref: orm.ArtifactRef | None):
        if not orm_ref:
            return None
        from app.schemas.labor import ArtifactRef
        return ArtifactRef(
            artifact_ref_id=orm_ref.artifact_ref_id,
            artifact_type=orm_ref.artifact_type,
            storage_system=orm_ref.storage_system,
            version=orm_ref.version,
            tenant_id=orm_ref.tenant_id,
            uri=orm_ref.uri,
            checksum=orm_ref.checksum,
            data_classification=orm_ref.data_classification,
            created_at=orm_ref.created_at,
            content=orm_ref.content,
        )

    return LaborCallResponse(
        labor_call_id=job.labor_call_id or "",
        job_id=job.job_id,
        status=JobState(job.status),
        stage=job.stage,
        executor_used=job.executor_used,
        started_at=job.started_at,
        completed_at=job.completed_at,
        raw_output_ref=_to_schema_ref(raw_ref),
        parsed_output_ref=_to_schema_ref(parsed_ref),
        validation_result_ref=_to_schema_ref(val_ref),
    )


async def _load_artifact_content(session: AsyncSession, principal: Principal, artifact_ref_id: str):
    ref = await session.get(orm.ArtifactRef, artifact_ref_id)
    if not ref:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "artifact not found")
    enforce_tenant(principal, ref.tenant_id)
    content = ref.content or {}
    return content.get("value", content)
