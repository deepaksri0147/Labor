"""Test harness: SQLite in-memory DB, fakeredis, a stub executor, auth disabled.

These let the suite run without Postgres/Redis while exercising the real handlers,
orchestrator, validation, and repair loop.
"""
from __future__ import annotations

import os

os.environ.setdefault("GATEWAY_AUTH_DISABLED", "true")
os.environ.setdefault("GATEWAY_DATABASE_URL", "sqlite+aiosqlite:///:memory:")

import pytest
import pytest_asyncio


@pytest_asyncio.fixture
async def app_client(monkeypatch):
    import fakeredis.aioredis as fakeredis
    from httpx import ASGITransport, AsyncClient

    from app.services import infra
    from app.services.executors import ExecutionResult, registry

    # --- swap Redis for fakeredis ---
    fake = fakeredis.FakeRedis(decode_responses=True)
    monkeypatch.setattr(infra, "_redis", fake)
    monkeypatch.setattr(infra, "get_redis", lambda: fake)

    # --- create schema on the in-memory DB ---
    from app.db import session as dbsession
    from app.models.orm import Base
    async with dbsession.engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)

    # --- register a deterministic stub executor (no network) ---
    class StubExecutor:
        executor_id = "stub"
        supports_batch = True
        supports_cache_prefix = True

        def __init__(self):
            self.mode = "good"  # or "bad_then_good"
            self.calls = 0

        async def execute(self, *, messages, parameters, cached_prefixes):
            self.calls += 1
            if self.mode == "bad_then_good" and self.calls == 1:
                text = '{"name": 123}'  # wrong type -> validation fails
            else:
                text = '{"name": "ok"}'
            return ExecutionResult(text, 10, 5, 0, 0, "stub", "test")

    stub = StubExecutor()
    registry._executors.clear()
    registry.register(stub)

    from app.main import app
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as client:
        yield client, stub, fake

    async with dbsession.engine.begin() as conn:
        await conn.run_sync(Base.metadata.drop_all)


def envelope(tenant="t1"):
    return {"tenant_id": tenant, "trace_context": {"traceparent": "00-abc-def-01"}}


def labor_call(tenant="t1", idem="k1", validation=False, repair_attempts=0, on_fail="fail"):
    return {
        "envelope": envelope(tenant),
        "labor_template_id": "lt1",
        "data_template_ref": {"artifact_ref_id": "dt1", "artifact_type": "data_template",
                              "storage_system": "DITA", "version": "1", "tenant_id": tenant},
        "output_du_class": "generated_artifact",
        "execution_target": {"kind": "executor", "executor_id": "stub"},
        "input_data": {"x": 1},
        "output_schema": {"type": "object", "required": ["name"],
                          "properties": {"name": {"type": "string"}}},
        "validation_required": validation,
        "repair_policy": {"max_attempts": repair_attempts},
        "idempotency_key": idem,
        "raw_output_persistence_policy": "immutable",
        "parsed_output_persistence_policy": "persist",
        "output_parse_mode": "json",
        "on_parse_failure": on_fail,
    }
