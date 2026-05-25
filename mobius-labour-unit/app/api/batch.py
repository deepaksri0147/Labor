"""Batch labor, cache prefixes, executor registry, executor + model policies.

Persistence delegated to the external data wrapper service.
"""
from __future__ import annotations

from collections import Counter
from datetime import timedelta

from fastapi import APIRouter, Depends, HTTPException, Response, status

from app.core.auth import Principal, current_principal, enforce_tenant
from app.core.config import get_settings
from app.core.enums import JobState
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
from app.services import wrapper_api, infra, orchestrator

_settings = get_settings()

batch_router = APIRouter(prefix="/labor/batch", tags=["Labor Batch"])
cache_router = APIRouter(prefix="/labor/cache-prefix", tags=["Cache Prefixes"])
exec_router = APIRouter(tags=["Executors"])


# ---- batch ----------------------------------------------------------------
@batch_router.post("", response_model=LaborBatchResponse)
async def submit_batch(req: LaborBatchRequest, response: Response,
                       principal: Principal = Depends(current_principal)) -> LaborBatchResponse:
    enforce_tenant(principal, req.envelope.tenant_id)
    if len(req.items) > _settings.max_batch_items:
        raise HTTPException(status.HTTP_400_BAD_REQUEST, f"batch exceeds max {_settings.max_batch_items} items")

    existing = await orchestrator.find_idempotent(req.envelope.tenant_id, "labor.batch", req.idempotency_key)
    if existing:
        response.status_code = status.HTTP_409_CONFLICT
        return await _batch_response(existing)

    batch_id = orchestrator.new_id("batch")
    batch = orm.LaborBatch(
        batch_id=batch_id, tenant_id=req.envelope.tenant_id, status=JobState.queued.value,
        submitted_count=len(req.items),
        failure_policy=req.failure_policy if isinstance(req.failure_policy, str) else req.failure_policy.value,
        concurrency_limit=req.concurrency_limit or _settings.default_batch_concurrency,
        executor_policy_id=req.executor_policy_id, idempotency_key=req.idempotency_key,
    )
    await wrapper_api.ingest(wrapper_api.LABOR_BATCH, [wrapper_api.orm_to_dict(batch)])
    await orchestrator.record_idempotent(req.envelope.tenant_id, "labor.batch", req.idempotency_key, batch_id)

    job_rows: list[dict] = []
    item_rows: list[dict] = []
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
        bi = orm.BatchItem(batch_id=batch_id, custom_id=item.custom_id,
                           labor_call_id=labor_call_id, job_id=job_id, status=JobState.queued.value)
        job_rows.append(wrapper_api.orm_to_dict(job))
        item_rows.append(wrapper_api.orm_to_dict(bi))
        job_ids.append(job_id)

    if job_rows:
        await wrapper_api.ingest(wrapper_api.LABOR_JOB, job_rows)
    if item_rows:
        # batch_item.id is autoincrement; strip Nones so the wrapper can assign
        await wrapper_api.ingest(wrapper_api.BATCH_ITEM, [{k: v for k, v in r.items() if v is not None} for r in item_rows])

    for jid in job_ids:
        await infra.enqueue_job(jid, token=wrapper_api.get_token())

    return LaborBatchResponse(
        batch_id=batch_id, status=JobState.queued, submitted_count=len(req.items),
        events_url=f"/labor/batch/{batch_id}/events", items_url=f"/labor/batch/{batch_id}/items",
        cancel_url=f"/labor/batch/{batch_id}/cancel",
    )


@batch_router.get("/{batch_id}", response_model=LaborBatchResponse)
async def get_batch(batch_id: str, principal: Principal = Depends(current_principal)) -> LaborBatchResponse:
    return await _batch_response(batch_id, principal)


@batch_router.get("/{batch_id}/items")
async def get_batch_items(batch_id: str, principal: Principal = Depends(current_principal),
                          page: int = 1, page_size: int = 100, status_filter: str | None = None) -> dict:
    batch = await wrapper_api.retrieve_one(wrapper_api.LABOR_BATCH, {"batch_id": batch_id})
    if not batch:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "batch not found")
    enforce_tenant(principal, batch["tenant_id"])
    items = wrapper_api.as_rows(await wrapper_api.retrieve(wrapper_api.BATCH_ITEM, {"batch_id": batch_id}))
    if status_filter:
        items = [it for it in items if it.get("status") == status_filter]
    start = (page - 1) * page_size
    page_items = items[start:start + page_size]
    results = []
    for it in page_items:
        job = None
        if it.get("job_id"):
            job = await wrapper_api.retrieve_one(wrapper_api.LABOR_JOB, {"job_id": it["job_id"]})
        results.append(LaborBatchItemResult(
            custom_id=it["custom_id"], labor_call_id=it.get("labor_call_id"),
            status=JobState(job["status"]) if job else JobState(it["status"]),
        ).model_dump(mode="json"))
    return {"items": results, "page": page, "page_size": page_size, "total": len(results)}


@batch_router.post("/{batch_id}/cancel", response_model=LaborBatchResponse)
async def cancel_batch(batch_id: str, principal: Principal = Depends(current_principal)) -> LaborBatchResponse:
    batch = await wrapper_api.retrieve_one(wrapper_api.LABOR_BATCH, {"batch_id": batch_id})
    if not batch:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "batch not found")
    enforce_tenant(principal, batch["tenant_id"])
    batch["cancel_requested"] = True
    await wrapper_api.update(wrapper_api.LABOR_BATCH, [batch])
    # cancel all non-terminal jobs
    jobs = wrapper_api.as_rows(await wrapper_api.retrieve(wrapper_api.LABOR_JOB, {"batch_id": batch_id}))
    dirty: list[dict] = []
    for j in jobs:
        if JobState(j["status"]) in {JobState.queued, JobState.scheduled, JobState.paused}:
            j["status"] = JobState.cancelled.value
            dirty.append(j)
        else:
            j["cancel_requested"] = True
            dirty.append(j)
    if dirty:
        await wrapper_api.update(wrapper_api.LABOR_JOB, dirty)
    return await _batch_response(batch_id)


async def _batch_response(batch_id: str, principal: Principal | None = None) -> LaborBatchResponse:
    batch = await wrapper_api.retrieve_one(wrapper_api.LABOR_BATCH, {"batch_id": batch_id})
    if not batch:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "batch not found")
    if principal:
        enforce_tenant(principal, batch["tenant_id"])
    jobs = wrapper_api.as_rows(await wrapper_api.retrieve(wrapper_api.LABOR_JOB, {"batch_id": batch_id}))
    counts = dict(Counter(j["status"] for j in jobs if j.get("status")))
    return LaborBatchResponse(
        batch_id=batch_id, status=JobState(batch["status"]), submitted_count=batch.get("submitted_count"),
        counts_by_state=counts, created_at=batch.get("created_at"),
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
                                 principal: Principal = Depends(current_principal)) -> ExecutorPolicy:
    enforce_tenant(principal, policy.tenant_id)
    pid = policy.executor_policy_id or orchestrator.new_id("ep")
    row = orm.ExecutorPolicy(executor_policy_id=pid, tenant_id=policy.tenant_id,
                             name=policy.name, spec=policy.model_dump(mode="json"))
    await wrapper_api.ingest(wrapper_api.EXECUTOR_POLICY, [wrapper_api.orm_to_dict(row)])
    return policy.model_copy(update={"executor_policy_id": pid})


@exec_router.post("/model-policies", response_model=ModelPolicy, tags=["Executors"])
async def create_model_policy(policy: ModelPolicy,
                              principal: Principal = Depends(current_principal)) -> ModelPolicy:
    enforce_tenant(principal, policy.tenant_id)
    pid = policy.model_policy_id or orchestrator.new_id("mp")
    row = orm.ModelPolicy(model_policy_id=pid, tenant_id=policy.tenant_id,
                          name=policy.name, spec=policy.model_dump(mode="json"))
    await wrapper_api.ingest(wrapper_api.MODEL_POLICY, [wrapper_api.orm_to_dict(row)])
    return policy.model_copy(update={"model_policy_id": pid})


@exec_router.get("/model-policies/{model_policy_id}", response_model=ModelPolicy, tags=["Executors"])
async def get_model_policy(model_policy_id: str,
                           principal: Principal = Depends(current_principal)) -> ModelPolicy:
    row = await wrapper_api.retrieve_one(wrapper_api.MODEL_POLICY, {"model_policy_id": model_policy_id})
    if not row:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "model policy not found")
    enforce_tenant(principal, row["tenant_id"])
    return ModelPolicy(**(row.get("spec") or {})).model_copy(update={"model_policy_id": row["model_policy_id"]})
