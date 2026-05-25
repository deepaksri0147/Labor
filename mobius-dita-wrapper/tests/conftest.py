"""DITA test harness: SQLite in-memory, fakeredis, stubbed render/labor/bob clients."""
from __future__ import annotations

import os

os.environ.setdefault("DITA_AUTH_DISABLED", "true")
os.environ.setdefault("DITA_DATABASE_URL", "sqlite+aiosqlite:///:memory:")

import pytest
import pytest_asyncio


@pytest_asyncio.fixture
async def app_client(monkeypatch):
    import fakeredis.aioredis as fakeredis
    from httpx import ASGITransport, AsyncClient

    from app.services import infra
    from app.services import clients

    fake = fakeredis.FakeRedis(decode_responses=True)
    monkeypatch.setattr(infra, "_redis", fake)
    monkeypatch.setattr(infra, "get_redis", lambda: fake)

    # stub the downstream clients (render engine, labor gateway, bob)
    async def fake_render(**kwargs):
        return {"content": "<html>rendered</html>", "content_type": "text/html"}

    async def fake_run_sync(call):
        return {"job_id": "job_stub", "status": "succeeded", "succeeded": True,
                "parsed_output_ref": "art_stub"}

    async def fake_run_async(call):
        return {"job_id": "job_stub", "status": "queued"}

    monkeypatch.setattr(clients.render_client, "render", fake_render)
    monkeypatch.setattr(clients.labor_client, "run_sync", fake_run_sync)
    monkeypatch.setattr(clients.labor_client, "run_async", fake_run_async)

    from app.db import session as dbsession
    from app.models.orm import Base
    async with dbsession.engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)

    from app.main import app
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as client:
        yield client, fake

    async with dbsession.engine.begin() as conn:
        await conn.run_sync(Base.metadata.drop_all)


H = {"x-tenant-id": "t1"}


def envelope(tenant="t1"):
    return {"tenant_id": tenant, "trace_context": {"traceparent": "00-abc-def-01"}}


def data_template(tenant="t1"):
    return {
        "tenant_id": tenant, "name": "dt", "template_type": "report",
        "input_schema": {"type": "object", "required": ["x"], "properties": {"x": {"type": "string"}}},
        "output_schema": {"type": "object"}, "required_variables": ["x"],
        "render_target": "html", "llm_required": True, "status": "active", "version": "1",
    }


def prompt_chain(tenant="t1"):
    return {
        "tenant_id": tenant, "name": "chain", "chain_type": "linear",
        "steps": [
            {"step_id": "s1", "step_type": "render_template", "input_mapping": {"x": "input.x"},
             "output_mapping": {}, "prompt_body": "Hello {{x}}"},
            {"step_id": "s2", "step_type": "call_llm", "input_mapping": {"prompt": "steps.s1.rendered"},
             "output_mapping": {}},
        ],
        "failure_policy": {}, "version": "1", "status": "active",
    }


def document_job(tenant="t1", job_type="render_bundle", idem="dj1"):
    return {
        "envelope": envelope(tenant), "job_type": job_type, "bundle_id": "b1",
        "format": "html", "idempotency_key": idem,
    }


def seed_packet(tenant="t1"):
    return {
        "tenant_id": tenant, "data_template_id": "dt1", "input_data": {"x": "1"},
        "output_artifact_type": "generated_artifact", "version": "1", "reentry_required": False,
        "status": "draft",
    }
