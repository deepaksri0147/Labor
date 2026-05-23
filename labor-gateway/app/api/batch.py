"""Batch labor, cache prefixes, executor registry, executor + model policies."""
from __future__ import annotations

from datetime import timedelta

from fastapi import APIRouter, Depends, HTTPException, Response, status
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.auth import Principal, current_principal, enforce_tenant
from app.core.config import get_settings
from app.core.enums import JobState
from app.db.session import get_session
from app.models import orm
from app.schemas.labor import (
    CachePrefix,
    CachePrefixCreateRequest,
    ExecutorPolicy,
    LaborBatchItemResult,
    LaborBatchRequest,
    LaborBatchResponse,
    ModelPolicy,
)
from app.services import infra, orchestrator

_settings = get_settings()

batch_router = APIRouter(prefix="/labor/batch", tags=["Labor Batch"])
cache_router = APIRouter(prefix="/labor/cache-prefix", tags=["Cache Prefixes"])
exec_router = APIRouter(tags=["Executors"])


# ---- batch ----------------------------------------------------------------
@batch_router.post("", response_model=LaborBatchResponse)
async def submit_batch(req: LaborBatchRequest, response: Response,
                       principal: Principal = Depends(current_principal),
                       session: AsyncSession = Depends(get_session)) -> LaborBatchResponse:
    enforce_tenant(principal, req.envelope.tenant_id)
    if len(req.items) > _settings.max_batch_items:
        raise HTTPException(status.HTTP_400_BAD_REQUEST, f"batch exceeds max {_settings.max_batch_items} items")

    existing = await orchestrator.find_idempotent(session, req.envelope.tenant_id, "labor.batch", req.idempotency_key)
    if existing:
        response.status_code = status.HTTP_409_CONFLICT
        return await _batch_response(session, existing)

    batch_id = orchestrator.new_id("batch")
    batch = orm.LaborBatch(
        batch_id=batch_id, tenant_id=req.envelope.tenant_id, status=JobState.queued.value,
        submitted_count=len(req.items),
        failure_policy=req.failure_policy if isinstance(req.failure_policy, str) else req.failure_policy.value,
        concurrency_limit=req.concurrency_limit or _settings.default_batch_concurrency,
        executor_policy_id=req.executor_policy_id, idempotency_key=req.idempotency_key,
    )
    session.add(batch)
    await orchestrator.record_idempotent(session, req.envelope.tenant_id, "labor.batch", req.idempotency_key, batch_id)
    await session.flush()

    job_ids: list[str] = []
    for item in req.items:
        labor_call_id = item.call.labor_call_id or orchestrator.new_id("lc")
        job_id = orchestrator.new_id("job")
        job = orm.LaborJob(
            job_id=job_id, tenant_id=req.envelope.tenant_id, batch_id=batch_id,
            labor_call_id=labor_call_id, status=JobState.queued.value, stage=item.call.stage,
            request_payload=item.call.model_dump(mode="json"),
            trace_context=item.call.envelope.trace_context.model_dump(),
        )
        session.add(job)
        session.add(orm.BatchItem(batch_id=batch_id, custom_id=item.custom_id,
                                  labor_call_id=labor_call_id, job_id=job_id, status=JobState.queued.value))
        job_ids.append(job_id)
    await session.commit()
    for jid in job_ids:
        await infra.enqueue_job(jid)

    return LaborBatchResponse(
        batch_id=batch_id, status=JobState.queued, submitted_count=len(req.items),
        events_url=f"/labor/batch/{batch_id}/events", items_url=f"/labor/batch/{batch_id}/items",
        cancel_url=f"/labor/batch/{batch_id}/cancel",
    )


@batch_router.get("/{batch_id}", response_model=LaborBatchResponse)
async def get_batch(batch_id: str, principal: Principal = Depends(current_principal),
                    session: AsyncSession = Depends(get_session)) -> LaborBatchResponse:
    return await _batch_response(session, batch_id, principal)


@batch_router.get("/{batch_id}/items")
async def get_batch_items(batch_id: str, principal: Principal = Depends(current_principal),
                          session: AsyncSession = Depends(get_session),
                          page: int = 1, page_size: int = 100, status_filter: str | None = None) -> dict:
    batch = await session.get(orm.LaborBatch, batch_id)
    if not batch:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "batch not found")
    enforce_tenant(principal, batch.tenant_id)
    q = select(orm.BatchItem).where(orm.BatchItem.batch_id == batch_id)
    if status_filter:
        q = q.where(orm.BatchItem.status == status_filter)
    q = q.offset((page - 1) * page_size).limit(page_size)
    items = (await session.execute(q)).scalars().all()
    results = []
    for it in items:
        job = await session.get(orm.LaborJob, it.job_id) if it.job_id else None
        results.append(LaborBatchItemResult(
            custom_id=it.custom_id, labor_call_id=it.labor_call_id,
            status=JobState(job.status) if job else JobState(it.status),
        ).model_dump(mode="json"))
    return {"items": results, "page": page, "page_size": page_size, "total": len(results)}


@batch_router.post("/{batch_id}/cancel", response_model=LaborBatchResponse)
async def cancel_batch(batch_id: str, principal: Principal = Depends(current_principal),
                       session: AsyncSession = Depends(get_session)) -> LaborBatchResponse:
    batch = await session.get(orm.LaborBatch, batch_id)
    if not batch:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "batch not found")
    enforce_tenant(principal, batch.tenant_id)
    batch.cancel_requested = True
    # cancel all non-terminal jobs
    jobs = (await session.execute(
        select(orm.LaborJob).where(orm.LaborJob.batch_id == batch_id)
    )).scalars().all()
    for j in jobs:
        if JobState(j.status) in {JobState.queued, JobState.scheduled, JobState.paused}:
            j.status = JobState.cancelled.value
        else:
            j.cancel_requested = True
    await session.commit()
    return await _batch_response(session, batch_id)


async def _batch_response(session: AsyncSession, batch_id: str, principal: Principal | None = None) -> LaborBatchResponse:
    batch = await session.get(orm.LaborBatch, batch_id)
    if not batch:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "batch not found")
    if principal:
        enforce_tenant(principal, batch.tenant_id)
    rows = (await session.execute(
        select(orm.LaborJob.status, func.count()).where(orm.LaborJob.batch_id == batch_id).group_by(orm.LaborJob.status)
    )).all()
    counts = {s: int(c) for s, c in rows}
    return LaborBatchResponse(
        batch_id=batch_id, status=JobState(batch.status), submitted_count=batch.submitted_count,
        counts_by_state=counts, created_at=batch.created_at,
    )


# ---- cache prefix ---------------------------------------------------------
@cache_router.post("", response_model=CachePrefix)
async def create_cache_prefix(req: CachePrefixCreateRequest,
                              principal: Principal = Depends(current_principal)) -> CachePrefix:
    enforce_tenant(principal, req.tenant_id)
    cpid = orchestrator.new_id("cp")
    token_count = max(1, len(req.content) // 4)  # rough token estimate
    await infra.store_cache_prefix(cpid, req.content, req.ttl_seconds,
                                   {"name": req.name, "role": req.role, "tenant_id": req.tenant_id,
                                    "token_count": token_count})
    now = infra.utcnow()
    return CachePrefix(cache_prefix_id=cpid, name=req.name, ttl_seconds=req.ttl_seconds,
                       token_count=token_count, role=req.role, hit_count=0, created_at=now,
                       expires_at=now + timedelta(seconds=req.ttl_seconds))


@cache_router.get("/{cache_prefix_id}", response_model=CachePrefix)
async def get_cache_prefix(cache_prefix_id: str,
                           principal: Principal = Depends(current_principal)) -> CachePrefix:
    data = await infra.get_cache_prefix(cache_prefix_id)
    if not data:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "cache prefix not found or expired")
    meta = data["meta"]
    enforce_tenant(principal, meta.get("tenant_id", principal.tenant_id))
    return CachePrefix(cache_prefix_id=cache_prefix_id, name=meta.get("name"),
                       ttl_seconds=data["ttl_seconds"], token_count=meta.get("token_count", 0),
                       role=meta.get("role"), hit_count=data["hit_count"])


# ---- executors + policies -------------------------------------------------
@exec_router.get("/executors", tags=["Executors"])
async def list_executors(principal: Principal = Depends(current_principal)) -> dict:
    from app.services.executors import registry
    return {"executors": registry.descriptors()}


@exec_router.post("/executor-policies", response_model=ExecutorPolicy, tags=["Executors"])
async def create_executor_policy(policy: ExecutorPolicy,
                                 principal: Principal = Depends(current_principal),
                                 session: AsyncSession = Depends(get_session)) -> ExecutorPolicy:
    enforce_tenant(principal, policy.tenant_id)
    pid = policy.executor_policy_id or orchestrator.new_id("ep")
    session.add(orm.ExecutorPolicy(executor_policy_id=pid, tenant_id=policy.tenant_id,
                                   name=policy.name, spec=policy.model_dump(mode="json")))
    out = policy.model_copy(update={"executor_policy_id": pid})
    return out


@exec_router.post("/model-policies", response_model=ModelPolicy, tags=["Executors"])
async def create_model_policy(policy: ModelPolicy,
                              principal: Principal = Depends(current_principal),
                              session: AsyncSession = Depends(get_session)) -> ModelPolicy:
    enforce_tenant(principal, policy.tenant_id)
    pid = policy.model_policy_id or orchestrator.new_id("mp")
    session.add(orm.ModelPolicy(model_policy_id=pid, tenant_id=policy.tenant_id,
                                name=policy.name, spec=policy.model_dump(mode="json")))
    return policy.model_copy(update={"model_policy_id": pid})


@exec_router.get("/model-policies/{model_policy_id}", response_model=ModelPolicy, tags=["Executors"])
async def get_model_policy(model_policy_id: str, principal: Principal = Depends(current_principal),
                           session: AsyncSession = Depends(get_session)) -> ModelPolicy:
    row = await session.get(orm.ModelPolicy, model_policy_id)
    if not row:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "model policy not found")
    enforce_tenant(principal, row.tenant_id)
    return ModelPolicy(**row.spec).model_copy(update={"model_policy_id": row.model_policy_id})
