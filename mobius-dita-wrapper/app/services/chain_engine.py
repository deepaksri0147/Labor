"""Prompt-chain execution engine.

Executes a chain's steps in dependency order (linear or DAG via edges). Each step type
maps to an action:
  render_template   -> render via prompt-template substitution (local)
  call_llm          -> one labor call to the Labor Gateway
  validate_output   -> JSON-schema validation (typed errors)
  repair_output     -> re-run the prior call_llm with errors (delegated to the gateway)
  render_document   -> call the existing render engine
  persist_output    -> persist an artifact
  request_human_review / branch / join / emit_event / call_external_api -> control/aux

Bounded re-entry: a chain run carries a depth counter; recursive chains that exceed
max_depth halt with failure rather than looping. Step outputs are accumulated and made
available to later steps via input_mapping.
"""
from __future__ import annotations

import json
from typing import Any

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.config import get_settings
from app.core.enums import EventType, JobState, StepType
from app.models import orm
from app.services import common
from app.services.clients import labor_client, render_client
from app.services.validation import validate_against_schema

_settings = get_settings()


async def run_chain(session: AsyncSession, run: orm.PromptChainRun) -> None:
    chain = (await session.execute(
        select(orm.VersionedAsset).where(
            orm.VersionedAsset.asset_id == run.prompt_chain_id,
            orm.VersionedAsset.asset_kind == "prompt_chain",
        )
    )).scalar_one_or_none()
    if chain is None:
        await _finish(session, run, JobState.failed, "chain definition not found")
        return

    if run.depth > (chain.spec.get("max_depth") or _settings.max_chain_depth):
        await _finish(session, run, JobState.failed, f"max chain depth exceeded ({run.depth})")
        return

    run.status = JobState.running.value
    await common.emit(session, job_id=run.prompt_chain_run_id, tenant_id=run.tenant_id,
                      trace_context={}, event_type=EventType.job_started, status=JobState.running, progress=0.0)

    steps = chain.spec.get("steps", [])
    order = _topo_order(steps, chain.spec.get("edges") or [])
    outputs: dict[str, Any] = dict(run.step_outputs or {})
    ctx = {"input": run.input_data, "steps": outputs}

    total = max(1, len(order))
    for i, step_id in enumerate(order):
        await session.refresh(run)
        if run.cancel_requested:
            await _finish(session, run, JobState.cancelled, "cancelled")
            return
        step = next((s for s in steps if s["step_id"] == step_id), None)
        if step is None:
            continue
        run.current_step_id = step_id
        await common.emit(session, job_id=run.prompt_chain_run_id, tenant_id=run.tenant_id, trace_context={},
                          event_type=EventType.step_started, status=JobState.running,
                          progress=round(100 * i / total, 1), message=f"step {step_id} ({step['step_type']})")
        try:
            result = await _run_step(session, run, step, ctx)
        except Exception as e:
            await _finish(session, run, JobState.failed, f"step {step_id} failed: {e}")
            return
        outputs[step_id] = result
        ctx["steps"] = outputs
        run.step_outputs = outputs
        await session.flush()
        await common.emit(session, job_id=run.prompt_chain_run_id, tenant_id=run.tenant_id, trace_context={},
                          event_type=EventType.step_completed, status=JobState.running,
                          progress=round(100 * (i + 1) / total, 1))

    await _finish(session, run, JobState.succeeded, None, progress=100.0)


async def _run_step(session: AsyncSession, run: orm.PromptChainRun, step: dict, ctx: dict) -> Any:
    st = StepType(step["step_type"])
    mapped_input = _apply_mapping(step.get("input_mapping") or {}, ctx)

    if st == StepType.call_llm:
        labor_call = _labor_call_for_step(run, step, mapped_input)
        result = await labor_client.run_sync(labor_call)
        await common.persist_artifact(session, tenant_id=run.tenant_id, artifact_type="parsed_model_output",
                                      content=result, chain_run_id=run.prompt_chain_run_id)
        return result

    if st == StepType.render_template:
        return {"rendered": _render_template_local(step, mapped_input)}

    if st == StepType.render_document:
        cfg = step.get("render_config") or {}
        auth = (run.input_data or {}).get("__auth_header")
        return await render_client.render(
            bundle_id=step.get("dita_bundle_id") or cfg.get("bundle_id"),
            chunk_ids=step.get("dita_chunk_ids") or cfg.get("chunk_ids"),
            data=mapped_input.get("data") if isinstance(mapped_input.get("data"), dict) else mapped_input,
            chunk_data=mapped_input.get("chunk_data") or cfg.get("chunk_data"),
            fmt=cfg.get("format", "html"),
            is_global=cfg.get("is_global"),
            pdf_options=cfg.get("pdfOptions"),
            bq_id=cfg.get("bqId"),
            bq_version=cfg.get("bqVersion"),
            db_type=cfg.get("dbType"),
            cohort_id=cfg.get("cohortId"),
            cohort_version=cfg.get("cohortVersion"),
            encrypt=bool(cfg.get("encrypt", False)),
            auth_header=auth,
        )

    if st == StepType.validate_output:
        schema = mapped_input.get("schema") or {}
        instance = mapped_input.get("value")
        errors = validate_against_schema(instance, schema) if isinstance(schema, dict) and schema else []
        return {"status": "passed" if not errors else "failed", "errors": [e.model_dump() for e in errors]}

    if st == StepType.repair_output:
        # delegate repair to the gateway by issuing another call_llm with the errors
        labor_call = _labor_call_for_step(run, step, mapped_input)
        return await labor_client.run_sync(labor_call)

    if st == StepType.persist_output:
        ref = await common.persist_artifact(session, tenant_id=run.tenant_id,
                                            artifact_type="generated_artifact", content=mapped_input,
                                            chain_run_id=run.prompt_chain_run_id)
        return {"artifact_ref_id": ref.artifact_ref_id}

    if st in (StepType.branch, StepType.join, StepType.emit_event, StepType.request_human_review,
              StepType.call_external_api):
        # control/aux steps: record and continue (human review would suspend in a Camunda-driven run)
        return {"step_type": st.value, "ok": True}

    return {"unhandled_step_type": st.value}


def _labor_call_for_step(run: orm.PromptChainRun, step: dict, mapped_input: dict) -> dict:
    return {
        "envelope": {"tenant_id": run.tenant_id, "trace_context": {"traceparent": f"00-{run.prompt_chain_run_id}-00-01"}},
        "labor_template_id": step.get("prompt_template_id") or "chain.call_llm",
        "data_template_ref": {"artifact_ref_id": step.get("data_template_id") or "chain-inline",
                              "artifact_type": "data_template", "storage_system": "DITA", "version": "1",
                              "tenant_id": run.tenant_id},
        "output_du_class": "generated_artifact",
        "execution_target": {"kind": "auto"},
        "input_data": mapped_input,
        "output_schema": {"type": "object"},
        "validation_required": bool(step.get("validation_schema_ref")),
        "idempotency_key": common.new_id("lk"),
        "raw_output_persistence_policy": "immutable",
        "parsed_output_persistence_policy": "persist",
        "output_parse_mode": "json",
        "on_parse_failure": "repair",
    }


def _render_template_local(step: dict, data: dict) -> str:
    body = step.get("prompt_body") or (step.get("render_config") or {}).get("prompt_body", "")
    for k, v in (data or {}).items():
        body = body.replace("{{" + str(k) + "}}", str(v))
    return body


def _apply_mapping(mapping: dict, ctx: dict) -> dict:
    """Resolve an input_mapping of {target: 'input.x' | 'steps.s1.field'} against ctx."""
    out: dict[str, Any] = {}
    for target, source in mapping.items():
        if isinstance(source, str) and "." in source:
            cur: Any = ctx
            for part in source.split("."):
                if isinstance(cur, dict):
                    cur = cur.get(part)
                else:
                    cur = None
                    break
            out[target] = cur
        else:
            out[target] = source
    return out


def _topo_order(steps: list[dict], edges: list[dict]) -> list[str]:
    ids = [s["step_id"] for s in steps]
    if not edges:
        return ids  # linear
    indeg = {i: 0 for i in ids}
    adj: dict[str, list[str]] = {i: [] for i in ids}
    for e in edges:
        a, b = e["from_step_id"], e["to_step_id"]
        if a in adj and b in indeg:
            adj[a].append(b)
            indeg[b] += 1
    queue = [i for i in ids if indeg[i] == 0]
    order: list[str] = []
    while queue:
        n = queue.pop(0)
        order.append(n)
        for m in adj[n]:
            indeg[m] -= 1
            if indeg[m] == 0:
                queue.append(m)
    # append any steps not covered (cycle-safe fallback)
    for i in ids:
        if i not in order:
            order.append(i)
    return order


async def _finish(session: AsyncSession, run: orm.PromptChainRun, status: JobState, reason: str | None, progress: float | None = None) -> None:
    run.status = status.value
    if reason:
        run.failure_reason = reason
    et = {JobState.succeeded: EventType.job_succeeded, JobState.failed: EventType.job_failed,
          JobState.cancelled: EventType.job_cancelled}.get(status, EventType.job_progress)
    await common.emit(session, job_id=run.prompt_chain_run_id, tenant_id=run.tenant_id, trace_context={},
                      event_type=et, status=status, progress=progress, message=reason)
