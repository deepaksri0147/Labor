"""Acceptance tests for the labor gateway.

These drive the real handlers + orchestrator. The worker loop is invoked inline so a
single test can submit a job and then drain it deterministically.
"""
from __future__ import annotations

import pytest

from tests.conftest import labor_call

pytestmark = pytest.mark.asyncio


async def _drain(fake):
    """Run queued jobs through the worker handler until the queue is empty."""
    from app.worker.main import handle
    while True:
        res = await fake.blpop(["labor:jobs"], timeout=1)
        if res is None:
            break
        _, job_id = res
        await handle(job_id)


async def test_submit_and_succeed(app_client):
    client, stub, fake = app_client
    r = await client.post("/labor/calls", json=labor_call(), headers={"x-tenant-id": "t1"})
    assert r.status_code == 200
    job_id = r.json()["job_id"]
    assert r.json()["status"] == "queued"

    await _drain(fake)

    j = await client.get(f"/labor/jobs/{job_id}", headers={"x-tenant-id": "t1"})
    assert j.json()["status"] == "succeeded"


async def test_idempotency_replay_returns_original(app_client):
    client, stub, fake = app_client
    h = {"x-tenant-id": "t1"}
    r1 = await client.post("/labor/calls", json=labor_call(idem="dup"), headers=h)
    r2 = await client.post("/labor/calls", json=labor_call(idem="dup"), headers=h)
    assert r1.json()["job_id"] == r2.json()["job_id"]
    assert r2.status_code == 409  # replay signalled, original returned


async def test_cross_tenant_read_forbidden(app_client):
    client, stub, fake = app_client
    r = await client.post("/labor/calls", json=labor_call(tenant="t1"), headers={"x-tenant-id": "t1"})
    job_id = r.json()["job_id"]
    # a different tenant must not read it
    other = await client.get(f"/labor/jobs/{job_id}", headers={"x-tenant-id": "t2"})
    assert other.status_code == 403


async def test_validation_repairs_then_succeeds(app_client):
    client, stub, fake = app_client
    stub.mode = "bad_then_good"  # first output fails schema, repair fixes it
    r = await client.post(
        "/labor/calls",
        json=labor_call(idem="rep", validation=True, repair_attempts=1),
        headers={"x-tenant-id": "t1"},
    )
    job_id = r.json()["job_id"]
    await _drain(fake)
    j = await client.get(f"/labor/jobs/{job_id}", headers={"x-tenant-id": "t1"})
    assert j.json()["status"] == "succeeded"
    assert stub.calls >= 2  # original + at least one repair


async def test_validation_exhausts_budget_terminal_repair_required(app_client):
    client, stub, fake = app_client

    # executor that is always wrong -> repair budget exhausts -> terminal repair_required
    async def always_bad(*, messages, parameters, cached_prefixes):
        from app.services.executors import ExecutionResult
        return ExecutionResult('{"name": 999}', 1, 1, 0, 0, "stub", "test")

    stub.execute = always_bad  # type: ignore
    r = await client.post(
        "/labor/calls",
        json=labor_call(idem="bad", validation=True, repair_attempts=1),
        headers={"x-tenant-id": "t1"},
    )
    job_id = r.json()["job_id"]
    await _drain(fake)
    j = await client.get(f"/labor/jobs/{job_id}", headers={"x-tenant-id": "t1"})
    assert j.json()["status"] == "repair_required"  # terminal, not an infinite loop


async def test_batch_returns_custom_ids_verbatim(app_client):
    client, stub, fake = app_client
    body = {
        "envelope": {"tenant_id": "t1", "trace_context": {"traceparent": "00-a-b-01"}},
        "idempotency_key": "batch1",
        "items": [
            {"custom_id": "alpha", "call": labor_call(idem="i-a")},
            {"custom_id": "beta", "call": labor_call(idem="i-b")},
        ],
    }
    r = await client.post("/labor/batch", json=body, headers={"x-tenant-id": "t1"})
    assert r.status_code == 200
    batch_id = r.json()["batch_id"]
    await _drain(fake)
    items = await client.get(f"/labor/batch/{batch_id}/items", headers={"x-tenant-id": "t1"})
    returned = {it["custom_id"] for it in items.json()["items"]}
    assert returned == {"alpha", "beta"}  # echoed verbatim


async def test_cache_prefix_roundtrip(app_client):
    client, stub, fake = app_client
    r = await client.post("/labor/cache-prefix",
                          json={"tenant_id": "t1", "content": "X" * 400, "ttl_seconds": 3600},
                          headers={"x-tenant-id": "t1"})
    assert r.status_code == 200
    cpid = r.json()["cache_prefix_id"]
    g = await client.get(f"/labor/cache-prefix/{cpid}", headers={"x-tenant-id": "t1"})
    assert g.json()["token_count"] == 100  # 400 chars / 4
