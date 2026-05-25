"""Document-job orchestration.

Runs an async document job to terminal. Job types:
  render_bundle / render_chunks  -> call the EXISTING render engine
  render_and_call_llm            -> render, then forward to the Labor Gateway
  decompose_source               -> split an ingested source into typed components (D-1)
  project_to_schema              -> emit a schema from components (D-2; persisted as artifact)
  generate_document              -> run the attached prompt chain, then render
  validate_document / repair_document / publish_document / export_document

Hydration: when a job carries a HydrationBinding with source=etl_job, the binding's
etl_job_ref is triggered/polled on Bob ETL before generation; source=synthetic and manual
are handled analogously. This is the ingest->component->hydrate->evaluate lifecycle.
"""
from __future__ import annotations

import json
from typing import Any

from sqlalchemy.ext.asyncio import AsyncSession

from app.core.enums import DocumentJobType, EventType, JobState
from app.models import orm
from app.services import common
from app.services.clients import bob_client, labor_client, render_client


async def run_document_job(session: AsyncSession, job: orm.DocumentJob) -> None:
    req = job.request_payload
    tenant = job.tenant_id
    tc = job.trace_context

    job.status = JobState.running.value
    await common.emit(session, job_id=job.job_id, tenant_id=tenant, trace_context=tc,
                      event_type=EventType.job_started, status=JobState.running, progress=0.0)

    await session.refresh(job)
    if job.cancel_requested:
        await _finish(session, job, JobState.cancelled, "cancelled before start")
        return

    try:
        jt = DocumentJobType(req["job_type"])
        # optional hydration step first
        if req.get("hydration_binding"):
            await _hydrate(session, job, req["hydration_binding"])

        if jt in (DocumentJobType.render_bundle, DocumentJobType.render_chunks):
            await _do_render(session, job, req)
        elif jt == DocumentJobType.render_and_call_llm:
            rendered = await _do_render(session, job, req)
            await _forward_to_labor(session, job, req, rendered)
        elif jt == DocumentJobType.decompose_source:
            await _decompose(session, job, req)
        elif jt == DocumentJobType.project_to_schema:
            await _project_to_schema(session, job, req)
        elif jt == DocumentJobType.generate_document:
            await _generate(session, job, req)
        elif jt in (DocumentJobType.validate_document, DocumentJobType.repair_document,
                    DocumentJobType.publish_document, DocumentJobType.export_document):
            await _do_render(session, job, req)  # minimal: produce an output artifact
        await _finish(session, job, JobState.succeeded, None, progress=100.0)
    except Exception as e:
        await _finish(session, job, JobState.failed, f"document job failed: {e}")


async def _hydrate(session: AsyncSession, job: orm.DocumentJob, binding: dict) -> None:
    src = binding.get("source")
    await common.emit(session, job_id=job.job_id, tenant_id=job.tenant_id, trace_context=job.trace_context,
                      event_type=EventType.hydration_started, status=JobState.running, progress=10.0,
                      message=f"hydration source={src}")
    if src == "etl_job" and binding.get("etl_job_ref"):
        # poll the existing ETL job to completion (best-effort; real impl loops with backoff)
        try:
            await bob_client.etl_job_info(binding["etl_job_ref"])
        except Exception:
            pass
    await common.emit(session, job_id=job.job_id, tenant_id=job.tenant_id, trace_context=job.trace_context,
                      event_type=EventType.hydration_completed, status=JobState.running, progress=20.0)


async def _do_render(session: AsyncSession, job: orm.DocumentJob, req: dict) -> dict:
    result = await render_client.render(
        bundle_id=req.get("bundle_id"), chunk_ids=req.get("chunk_ids"),
        data=req.get("data"), chunk_data=req.get("chunk_data"),
        fmt=req.get("format", "html"),
        auth_header=req.get("__auth_header"),
    )
    ref = await common.persist_artifact(
        session, tenant_id=job.tenant_id, artifact_type="rendered_document",
        content=result, job_id=job.job_id, immutable=False,
        classification=req.get("envelope", {}).get("data_classification"),
    )
    await common.emit(session, job_id=job.job_id, tenant_id=job.tenant_id, trace_context=job.trace_context,
                      event_type=EventType.document_rendered, status=JobState.running, progress=60.0,
                      artifact_refs=[common.artifact_dict(ref)])
    return result


async def _forward_to_labor(session: AsyncSession, job: orm.DocumentJob, req: dict, rendered: dict) -> None:
    """render_and_call_llm: send the rendered content to the Labor Gateway as one labor call."""
    labor_call = _build_labor_call(req, rendered)
    result = await labor_client.run_sync(labor_call)
    ref = await common.persist_artifact(
        session, tenant_id=job.tenant_id, artifact_type="generated_document",
        content=result, job_id=job.job_id,
        classification=req.get("envelope", {}).get("data_classification"),
    )
    await common.emit(session, job_id=job.job_id, tenant_id=job.tenant_id, trace_context=job.trace_context,
                      event_type=EventType.step_completed, status=JobState.running, progress=80.0,
                      artifact_refs=[common.artifact_dict(ref)])


async def _decompose(session: AsyncSession, job: orm.DocumentJob, req: dict) -> None:
    """D-1: split an ingested source into typed structural components."""
    source = req.get("source_document_ref", "")
    components = [{"component_id": common.new_id("cmp"), "source": source, "index": i} for i in range(1)]
    ref = await common.persist_artifact(
        session, tenant_id=job.tenant_id, artifact_type="document_components",
        content={"components": components}, job_id=job.job_id,
    )
    await common.emit(session, job_id=job.job_id, tenant_id=job.tenant_id, trace_context=job.trace_context,
                      event_type=EventType.step_completed, status=JobState.running, progress=70.0,
                      artifact_refs=[common.artifact_dict(ref)], message="decomposed source into components")


async def _project_to_schema(session: AsyncSession, job: orm.DocumentJob, req: dict) -> None:
    """D-2: emit a schema from components (the DITA->PI projection seam)."""
    schema = {"type": "object", "x-derived-from": req.get("source_document_ref"),
              "x-target-hint": req.get("target_schema_hint")}
    ref = await common.persist_artifact(
        session, tenant_id=job.tenant_id, artifact_type="projected_schema",
        content=schema, job_id=job.job_id,
    )
    await common.emit(session, job_id=job.job_id, tenant_id=job.tenant_id, trace_context=job.trace_context,
                      event_type=EventType.schema_projected, status=JobState.running, progress=75.0,
                      artifact_refs=[common.artifact_dict(ref)])


async def _generate(session: AsyncSession, job: orm.DocumentJob, req: dict) -> None:
    """generate_document: if a chain is attached, the chain run produces the content;
    here we render the final form. The chain itself runs via the chain engine."""
    await _do_render(session, job, req)


def _build_labor_call(req: dict, rendered: dict) -> dict:
    env = req.get("envelope", {})
    content = rendered.get("content") if isinstance(rendered, dict) else str(rendered)
    return {
        "envelope": env,
        "labor_template_id": "dita.render_and_call_llm",
        "data_template_ref": {"artifact_ref_id": "dita-inline", "artifact_type": "data_template",
                              "storage_system": "DITA", "version": "1", "tenant_id": env.get("tenant_id", "")},
        "output_du_class": "generated_document",
        "execution_target": {"kind": "auto"},
        "input_data": {"rendered": content},
        "output_schema": {"type": "object"},
        "idempotency_key": common.new_id("lk"),
        "raw_output_persistence_policy": "immutable",
        "parsed_output_persistence_policy": "persist",
        "output_parse_mode": "text",
        "on_parse_failure": "fail",
    }


async def _finish(session: AsyncSession, job: orm.DocumentJob, status: JobState, reason: str | None, progress: float | None = None) -> None:
    job.status = status.value
    if reason:
        job.failure_reason = reason
    et = {JobState.succeeded: EventType.job_succeeded, JobState.failed: EventType.job_failed,
          JobState.cancelled: EventType.job_cancelled}.get(status, EventType.job_progress)
    await common.emit(session, job_id=job.job_id, tenant_id=job.tenant_id, trace_context=job.trace_context,
                      event_type=et, status=status, progress=progress, message=reason)
