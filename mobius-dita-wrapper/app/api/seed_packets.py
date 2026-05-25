"""Seed-packet endpoints: CRUD, render, to-llm (forward to Labor Gateway), status, lineage."""
from __future__ import annotations

from fastapi import APIRouter, Depends, Header, HTTPException, Request, status
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.auth import Principal, current_principal, enforce_tenant
from app.core.enums import EventType, JobState, SeedPacketStatus  # JobState used in to-llm chain wiring
from app.db.session import get_session
from app.models import orm
from app.schemas.dita import SeedPacket, SeedPacketStatusUpdate
from app.services import common, infra
from app.services.clients import labor_client, render_client

router = APIRouter(prefix="/seed-packets", tags=["Seed Packets"])
SEC = [{"bearerAuth": []}]


async def _load(session, sid, principal) -> orm.SeedPacket:
    sp = await session.get(orm.SeedPacket, sid)
    if not sp:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "seed packet not found")
    enforce_tenant(principal, sp.tenant_id)
    return sp


@router.post("", response_model=SeedPacket)
async def create_seed_packet(sp: SeedPacket, principal: Principal = Depends(current_principal),
                             session: AsyncSession = Depends(get_session)) -> SeedPacket:
    enforce_tenant(principal, sp.tenant_id)
    sid = sp.seed_packet_id or common.new_id("seed")
    out = sp.model_copy(update={"seed_packet_id": sid})
    row = orm.SeedPacket(
        seed_packet_id=sid, tenant_id=sp.tenant_id, data_template_id=sp.data_template_id,
        status=sp.status if isinstance(sp.status, str) else sp.status.value,
        document_workflow_id=sp.document_workflow_id, prompt_chain_run_id=sp.prompt_chain_run_id,
        reentry_required=sp.reentry_required, spec=out.model_dump(mode="json"), version=sp.version,
    )
    session.add(row)
    return out


@router.get("/{seed_packet_id}", response_model=SeedPacket)
async def get_seed_packet(seed_packet_id: str, principal: Principal = Depends(current_principal),
                          session: AsyncSession = Depends(get_session)) -> SeedPacket:
    sp = await _load(session, seed_packet_id, principal)
    return SeedPacket(**sp.spec)


@router.put("/{seed_packet_id}", response_model=SeedPacket)
async def update_seed_packet(seed_packet_id: str, sp: SeedPacket, if_match: str | None = Header(default=None),
                             principal: Principal = Depends(current_principal),
                             session: AsyncSession = Depends(get_session)) -> SeedPacket:
    row = await _load(session, seed_packet_id, principal)
    if if_match and if_match != row.version:
        raise HTTPException(status.HTTP_409_CONFLICT, "version conflict")
    new_version = str(int(float(row.version)) + 1)
    out = sp.model_copy(update={"seed_packet_id": seed_packet_id, "version": new_version})
    row.spec = out.model_dump(mode="json"); row.version = new_version
    row.status = out.status if isinstance(out.status, str) else out.status.value
    row.document_workflow_id = sp.document_workflow_id; row.reentry_required = sp.reentry_required
    return out


@router.post("/{seed_packet_id}/render")
async def render_seed_packet(seed_packet_id: str, request: Request,
                             format: str = "html",
                             principal: Principal = Depends(current_principal),
                             session: AsyncSession = Depends(get_session)) -> dict:
    sp = await _load(session, seed_packet_id, principal)
    spec = sp.spec
    auth = request.headers.get("authorization")
    fmt = spec.get("render_format") or format
    result = await render_client.render(
        bundle_id=spec.get("bundle_id"), chunk_ids=spec.get("chunk_ids"),
        data=spec.get("input_data"), chunk_data=spec.get("chunk_data"),
        fmt=fmt,
        auth_header=auth,
    )
    ref = await common.persist_artifact(session, tenant_id=sp.tenant_id, artifact_type="rendered_seed_packet",
                                        content=result)
    sp.status = SeedPacketStatus.rendered.value
    # Persist rendered artifact ref + format back on the spec so subsequent
    # /to-llm and /lineage calls can recover the link.
    sp.spec = {**spec, "status": sp.status, "rendered_artifact_ref": ref.artifact_ref_id, "render_format": fmt}
    return {"seed_packet_id": seed_packet_id, "rendered_artifact_ref": ref.artifact_ref_id,
            "status": sp.status, "format": fmt, "result": result}


@router.post("/{seed_packet_id}/to-llm")
async def seed_packet_to_llm(seed_packet_id: str, principal: Principal = Depends(current_principal),
                             session: AsyncSession = Depends(get_session)) -> dict:
    """Dispatch the seed packet to the Labor Gateway as one labor call.

    If the seed carries a prompt_chain_id, also start a PromptChainRun and link
    its id back onto the seed (so subsequent reads see the chain run).
    If document_workflow_id is set, the labor_call_id is mirrored into doc.refs.
    """
    sp = await _load(session, seed_packet_id, principal)
    spec = sp.spec
    labor_call = {
        "envelope": {"tenant_id": sp.tenant_id, "trace_context": {"traceparent": f"00-{seed_packet_id}-00-01"}},
        "labor_template_id": spec.get("prompt_template_id") or "seed.to_llm",
        "data_template_ref": {"artifact_ref_id": spec.get("data_template_id", "seed-inline"),
                              "artifact_type": "data_template", "storage_system": "DITA", "version": "1",
                              "tenant_id": sp.tenant_id},
        "output_du_class": spec.get("output_artifact_type", "generated_artifact"),
        "execution_target": {"kind": "auto"},
        "input_data": spec.get("input_data", {}),
        "output_schema": {"type": "object"},
        "validation_required": bool(spec.get("validation_schema_ref")),
        "idempotency_key": common.new_id("lk"),
        "raw_output_persistence_policy": "immutable",
        "parsed_output_persistence_policy": "persist",
        "output_parse_mode": "json",
        "on_parse_failure": "repair",
    }
    try:
        result = await labor_client.run_async(labor_call)
    except Exception as e:
        raise HTTPException(status.HTTP_502_BAD_GATEWAY,
                            f"labor gateway unreachable: {e}") from e
    labor_call_id = result.get("job_id")
    sp.status = SeedPacketStatus.sent_to_llm.value

    chain_id = spec.get("prompt_chain_id")
    chain_run_id: str | None = spec.get("prompt_chain_run_id")
    if chain_id and not chain_run_id:
        # Start a chain run carrying the seed's input_data; enqueue for worker.
        chain_run_id = common.new_id("pcr")
        session.add(orm.PromptChainRun(
            prompt_chain_run_id=chain_run_id, tenant_id=sp.tenant_id,
            prompt_chain_id=chain_id, job_id=chain_run_id,
            status=JobState.queued.value,
            input_data={**(spec.get("input_data") or {}), "__seed_packet_id": seed_packet_id},
            step_outputs={},
        ))
        sp.prompt_chain_run_id = chain_run_id
        await session.flush()
        await session.commit()
        await infra.enqueue_chain_run(chain_run_id)

    # Mirror the labor link onto the document workflow if attached.
    doc_workflow_id = spec.get("document_workflow_id") or sp.document_workflow_id
    if doc_workflow_id:
        doc = await session.get(orm.DocumentWorkflow, doc_workflow_id)
        if doc and doc.tenant_id == sp.tenant_id:
            doc.refs = {**(doc.refs or {}), "labor_call_id": labor_call_id,
                        "seed_packet_id": seed_packet_id}

    sp.spec = {**spec, "status": sp.status, "labor_call_id": labor_call_id,
               "prompt_chain_run_id": chain_run_id}
    return {"seed_packet_id": seed_packet_id, "labor_call_id": labor_call_id,
            "job_id": labor_call_id, "prompt_chain_run_id": chain_run_id,
            "document_workflow_id": doc_workflow_id,
            "status": result.get("status", JobState.queued.value)}


@router.put("/{seed_packet_id}/status", response_model=SeedPacket)
async def update_seed_packet_status(seed_packet_id: str, body: SeedPacketStatusUpdate,
                                    principal: Principal = Depends(current_principal),
                                    session: AsyncSession = Depends(get_session)) -> SeedPacket:
    sp = await _load(session, seed_packet_id, principal)
    new_status = body.status if isinstance(body.status, str) else body.status.value
    sp.status = new_status
    sp.spec = {**sp.spec, "status": new_status}
    await common.emit(session, job_id=seed_packet_id, tenant_id=sp.tenant_id, trace_context={},
                      event_type=EventType.step_completed, message=f"seed status -> {new_status}")
    return SeedPacket(**sp.spec)


@router.get("/{seed_packet_id}/lineage")
async def seed_packet_lineage(seed_packet_id: str, principal: Principal = Depends(current_principal),
                              session: AsyncSession = Depends(get_session)) -> dict:
    sp = await _load(session, seed_packet_id, principal)
    edges = (await session.execute(
        select(orm.LineageEdge).where(orm.LineageEdge.job_id == seed_packet_id)
    )).scalars().all()
    return {"seed_packet_id": seed_packet_id,
            "lineage_refs": sp.spec.get("lineage_refs", []),
            "edges": [{"from": e.from_artifact_ref, "to": e.to_artifact_ref, "edge_type": e.edge_type} for e in edges]}
