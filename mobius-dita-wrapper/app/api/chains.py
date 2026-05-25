"""Prompt-chain definitions and chain-run lifecycle."""
from __future__ import annotations

from fastapi import APIRouter, Depends, Header, HTTPException, Request, status
from fastapi.responses import StreamingResponse
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.auth import Principal, current_principal, enforce_tenant
from app.core.enums import TERMINAL_STATES, JobState
from app.db.session import get_session
from app.models import orm
from app.schemas.dita import ChainExecuteRequest, ChainExecuteResponse, PromptChain
from app.services import common, infra
from app.api._sse import sse_event_stream

router = APIRouter(tags=["Prompt Chains"])
SEC = [{"bearerAuth": []}]


async def _load_chain(session, chain_id, principal) -> orm.VersionedAsset:
    a = (await session.execute(
        select(orm.VersionedAsset).where(orm.VersionedAsset.asset_id == chain_id, orm.VersionedAsset.asset_kind == "prompt_chain")
    )).scalar_one_or_none()
    if not a:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "prompt chain not found")
    enforce_tenant(principal, a.tenant_id)
    return a


@router.post("/prompt-chains", response_model=PromptChain)
async def create_chain(c: PromptChain, principal: Principal = Depends(current_principal),
                       session: AsyncSession = Depends(get_session)) -> PromptChain:
    enforce_tenant(principal, c.tenant_id)
    cid = c.prompt_chain_id or common.new_id("pc")
    out = c.model_copy(update={"prompt_chain_id": cid})
    asset = orm.VersionedAsset(asset_id=cid, asset_kind="prompt_chain", tenant_id=c.tenant_id,
                               name=c.name, version=c.version, status=c.status, spec=out.model_dump(mode="json"))
    session.add(asset)
    session.add(orm.AssetVersion(asset_id=cid, asset_kind="prompt_chain", version=c.version, spec=out.model_dump(mode="json")))
    return out


@router.get("/prompt-chains/{chain_id}", response_model=PromptChain)
async def get_chain(chain_id: str, principal: Principal = Depends(current_principal),
                    session: AsyncSession = Depends(get_session)) -> PromptChain:
    a = await _load_chain(session, chain_id, principal)
    return PromptChain(**a.spec)


@router.put("/prompt-chains/{chain_id}", response_model=PromptChain)
async def update_chain(chain_id: str, c: PromptChain, if_match: str | None = Header(default=None),
                       principal: Principal = Depends(current_principal),
                       session: AsyncSession = Depends(get_session)) -> PromptChain:
    a = await _load_chain(session, chain_id, principal)
    if if_match and if_match != a.version:
        raise HTTPException(status.HTTP_409_CONFLICT, "version conflict")
    new_version = str(int(float(a.version)) + 1)
    out = c.model_copy(update={"prompt_chain_id": chain_id, "version": new_version})
    a.spec = out.model_dump(mode="json"); a.version = new_version; a.status = c.status; a.name = c.name
    session.add(orm.AssetVersion(asset_id=chain_id, asset_kind="prompt_chain", version=new_version, spec=a.spec))
    return out


@router.get("/prompt-chains/{chain_id}/versions")
async def chain_versions(chain_id: str, principal: Principal = Depends(current_principal),
                         session: AsyncSession = Depends(get_session)) -> dict:
    await _load_chain(session, chain_id, principal)
    rows = (await session.execute(
        select(orm.AssetVersion.version, orm.AssetVersion.created_at).where(orm.AssetVersion.asset_id == chain_id)
    )).all()
    return {"prompt_chain_id": chain_id, "versions": [{"version": v, "created_at": c.isoformat()} for v, c in rows]}


@router.post("/prompt-chains/{chain_id}/archive")
async def archive_chain(chain_id: str, principal: Principal = Depends(current_principal),
                        session: AsyncSession = Depends(get_session)) -> dict:
    a = await _load_chain(session, chain_id, principal)
    a.status = "deprecated"; a.spec = {**a.spec, "status": "deprecated"}
    return {"prompt_chain_id": chain_id, "status": "deprecated"}


@router.post("/prompt-chains/{chain_id}/clone", response_model=PromptChain)
async def clone_chain(chain_id: str, principal: Principal = Depends(current_principal),
                      session: AsyncSession = Depends(get_session)) -> PromptChain:
    a = await _load_chain(session, chain_id, principal)
    nid = common.new_id("pc")
    spec = {**a.spec, "prompt_chain_id": nid, "version": "1", "status": "draft",
            "name": a.spec.get("name", "") + " (clone)"}
    clone = orm.VersionedAsset(asset_id=nid, asset_kind="prompt_chain", tenant_id=a.tenant_id,
                               name=spec["name"], version="1", status="draft", spec=spec)
    session.add(clone)
    session.add(orm.AssetVersion(asset_id=nid, asset_kind="prompt_chain", version="1", spec=spec))
    return PromptChain(**spec)


@router.post("/prompt-chains/{chain_id}/execute", response_model=ChainExecuteResponse)
async def execute_chain(chain_id: str, req: ChainExecuteRequest, request: Request,
                        principal: Principal = Depends(current_principal),
                        session: AsyncSession = Depends(get_session)) -> ChainExecuteResponse:
    a = await _load_chain(session, chain_id, principal)
    enforce_tenant(principal, req.envelope.tenant_id)

    existing = await common.find_idempotent(session, req.envelope.tenant_id, "chain.execute", req.idempotency_key)
    if existing:
        run = await session.get(orm.PromptChainRun, existing)
        return ChainExecuteResponse(prompt_chain_run_id=run.prompt_chain_run_id, job_id=run.job_id or run.prompt_chain_run_id,
                                    status=JobState(run.status))

    run_id = common.new_id("pcr")
    input_data = dict(req.input_data or {})
    auth = request.headers.get("authorization")
    if auth:
        # Worker forwards this on render_document / call_llm step calls.
        input_data["__auth_header"] = auth
    run = orm.PromptChainRun(prompt_chain_run_id=run_id, tenant_id=req.envelope.tenant_id,
                             prompt_chain_id=chain_id, job_id=run_id, status=JobState.queued.value,
                             input_data=input_data, step_outputs={})
    session.add(run)
    await common.record_idempotent(session, req.envelope.tenant_id, "chain.execute", req.idempotency_key, run_id)
    await session.flush()
    await session.commit()
    await infra.enqueue_chain_run(run_id)
    return ChainExecuteResponse(prompt_chain_run_id=run_id, job_id=run_id, status=JobState.queued,
                                events_url=f"/prompt-chain-runs/{run_id}/events",
                                artifacts_url=f"/prompt-chain-runs/{run_id}/artifacts")


# ---- chain run lifecycle --------------------------------------------------
async def _load_run(session, run_id, principal) -> orm.PromptChainRun:
    run = await session.get(orm.PromptChainRun, run_id)
    if not run:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "chain run not found")
    enforce_tenant(principal, run.tenant_id)
    return run


@router.get("/prompt-chain-runs/{run_id}")
async def get_run(run_id: str, principal: Principal = Depends(current_principal),
                  session: AsyncSession = Depends(get_session)) -> dict:
    run = await _load_run(session, run_id, principal)
    return {"prompt_chain_run_id": run.prompt_chain_run_id, "prompt_chain_id": run.prompt_chain_id,
            "status": run.status, "current_step_id": run.current_step_id, "depth": run.depth,
            "failure_reason": run.failure_reason}


@router.post("/prompt-chain-runs/{run_id}/cancel")
async def cancel_run(run_id: str, principal: Principal = Depends(current_principal),
                     session: AsyncSession = Depends(get_session)) -> dict:
    run = await _load_run(session, run_id, principal)
    if JobState(run.status) in TERMINAL_STATES:
        raise HTTPException(status.HTTP_409_CONFLICT, "run is terminal")
    run.cancel_requested = True
    if JobState(run.status) in {JobState.queued, JobState.scheduled}:
        run.status = JobState.cancelled.value
    return {"prompt_chain_run_id": run_id, "status": run.status}


@router.post("/prompt-chain-runs/{run_id}/retry")
async def retry_run(run_id: str, principal: Principal = Depends(current_principal),
                    session: AsyncSession = Depends(get_session)) -> dict:
    run = await _load_run(session, run_id, principal)
    if JobState(run.status) not in TERMINAL_STATES:
        raise HTTPException(status.HTTP_409_CONFLICT, "can only retry a terminal run")
    run.status = JobState.queued.value; run.failure_reason = None; run.cancel_requested = False
    await session.commit()
    await infra.enqueue_chain_run(run_id)
    return {"prompt_chain_run_id": run_id, "status": run.status}


@router.post("/prompt-chain-runs/{run_id}/fork")
async def fork_run(run_id: str, principal: Principal = Depends(current_principal),
                   session: AsyncSession = Depends(get_session)) -> dict:
    parent = await _load_run(session, run_id, principal)
    nid = common.new_id("pcr")
    fork = orm.PromptChainRun(prompt_chain_run_id=nid, tenant_id=parent.tenant_id,
                              prompt_chain_id=parent.prompt_chain_id, job_id=nid, status=JobState.queued.value,
                              depth=parent.depth + 1, input_data=parent.input_data, step_outputs={})
    session.add(fork)
    await session.commit()
    await infra.enqueue_chain_run(nid)
    return {"prompt_chain_run_id": nid, "forked_from": run_id, "status": "queued"}


@router.get("/prompt-chain-runs/{run_id}/events")
async def run_events(run_id: str, principal: Principal = Depends(current_principal),
                     session: AsyncSession = Depends(get_session)):
    await _load_run(session, run_id, principal)
    return StreamingResponse(sse_event_stream(run_id), media_type="text/event-stream")


@router.get("/prompt-chain-runs/{run_id}/artifacts")
async def run_artifacts(run_id: str, principal: Principal = Depends(current_principal),
                        session: AsyncSession = Depends(get_session)) -> dict:
    await _load_run(session, run_id, principal)
    refs = (await session.execute(
        select(orm.ArtifactRef).where(orm.ArtifactRef.prompt_chain_run_id == run_id)
    )).scalars().all()
    return {"run_id": run_id, "artifacts": [common.artifact_dict(r) for r in refs]}


@router.get("/prompt-chain-runs/{run_id}/lineage")
async def run_lineage(run_id: str, principal: Principal = Depends(current_principal),
                      session: AsyncSession = Depends(get_session)) -> dict:
    await _load_run(session, run_id, principal)
    edges = (await session.execute(
        select(orm.LineageEdge).where(orm.LineageEdge.job_id == run_id)
    )).scalars().all()
    return {"run_id": run_id, "edges": [{"from": e.from_artifact_ref, "to": e.to_artifact_ref,
                                          "edge_type": e.edge_type} for e in edges]}
