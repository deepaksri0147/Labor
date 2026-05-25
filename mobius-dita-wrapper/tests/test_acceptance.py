"""Acceptance tests for the DITA service."""
from __future__ import annotations

import pytest

from tests.conftest import H, data_template, document_job, prompt_chain, seed_packet

pytestmark = pytest.mark.asyncio


async def _drain_jobs(fake):
    from app.worker.main import _handle_doc_job, _handle_chain_run
    while True:
        jid = await fake.blpop(["dita:jobs"], timeout=1)
        if jid is None:
            break
        await _handle_doc_job(jid[1])
    while True:
        rid = await fake.blpop(["dita:chain-runs"], timeout=1)
        if rid is None:
            break
        await _handle_chain_run(rid[1])


async def test_data_template_crud_and_versioning(app_client):
    client, fake = app_client
    r = await client.post("/data-templates", json=data_template(), headers=H)
    assert r.status_code == 200
    tid = r.json()["template_id"]
    # update with a schema change -> new version
    body = data_template()
    body["input_schema"]["properties"]["y"] = {"type": "string"}
    u = await client.put(f"/data-templates/{tid}", json=body, headers=H)
    assert u.json()["version"] == "2"
    v = await client.get(f"/data-templates/{tid}/versions", headers=H)
    assert len(v.json()["versions"]) >= 2


async def test_validate_input_against_template(app_client):
    client, fake = app_client
    tid = (await client.post("/data-templates", json=data_template(), headers=H)).json()["template_id"]
    good = await client.post(f"/data-templates/{tid}/validate-input", json={"data": {"x": "ok"}}, headers=H)
    assert good.json()["valid"] is True
    bad = await client.post(f"/data-templates/{tid}/validate-input", json={"data": {}}, headers=H)
    assert bad.json()["valid"] is False


async def test_chain_execute_runs_to_success(app_client):
    client, fake = app_client
    cid = (await client.post("/prompt-chains", json=prompt_chain(), headers=H)).json()["prompt_chain_id"]
    r = await client.post(f"/prompt-chains/{cid}/execute",
                          json={"envelope": {"tenant_id": "t1", "trace_context": {"traceparent": "00-a-b-01"}},
                                "input_data": {"x": "world"}, "idempotency_key": "ce1", "execution_mode": "async"},
                          headers=H)
    assert r.status_code == 200
    run_id = r.json()["prompt_chain_run_id"]
    await _drain_jobs(fake)
    got = await client.get(f"/prompt-chain-runs/{run_id}", headers=H)
    assert got.json()["status"] == "succeeded"


async def test_document_job_renders(app_client):
    client, fake = app_client
    r = await client.post("/document-jobs", json=document_job(), headers=H)
    assert r.status_code == 200
    job_id = r.json()["job_id"]
    await _drain_jobs(fake)
    j = await client.get(f"/document-jobs/{job_id}", headers=H)
    assert j.json()["status"] == "succeeded"
    arts = await client.get(f"/document-jobs/{job_id}/artifacts", headers=H)
    assert arts.json()["rendered_outputs"]


async def test_document_job_idempotency_replay(app_client):
    client, fake = app_client
    r1 = await client.post("/document-jobs", json=document_job(idem="dup"), headers=H)
    r2 = await client.post("/document-jobs", json=document_job(idem="dup"), headers=H)
    assert r1.json()["job_id"] == r2.json()["job_id"]
    assert r2.status_code == 409


async def test_render_and_call_llm_uses_labor_gateway(app_client):
    client, fake = app_client
    r = await client.post("/document-jobs", json=document_job(job_type="render_and_call_llm", idem="rl"), headers=H)
    job_id = r.json()["job_id"]
    await _drain_jobs(fake)
    arts = await client.get(f"/document-jobs/{job_id}/artifacts", headers=H)
    assert arts.json()["generated_documents"]  # produced via the Labor Gateway stub


async def test_workflow_transition_guard(app_client):
    client, fake = app_client
    # create a document workflow row directly via the model (no create endpoint in fragment)
    from app.db.session import SessionLocal
    from app.models import orm
    async with SessionLocal() as s:
        s.add(orm.DocumentWorkflow(document_id="doc1", tenant_id="t1", workflow_state="draft"))
        await s.commit()
    # draft -> approve is illegal; must go draft -> ready_for_generation ... ready_for_review first
    bad = await client.post("/documents/doc1/approve", json={}, headers=H)
    assert bad.status_code == 409
    ok = await client.post("/documents/doc1/submit-review", json={}, headers=H)
    assert ok.status_code == 200
    assert ok.json()["workflow_state"] == "ready_for_review"


async def test_seed_packet_crud_and_to_llm(app_client):
    client, fake = app_client
    c = await client.post("/seed-packets", json=seed_packet(), headers=H)
    assert c.status_code == 200
    sid = c.json()["seed_packet_id"]
    r = await client.post(f"/seed-packets/{sid}/to-llm", headers=H)
    assert r.json()["status"] in ("queued", "sent_to_llm")
    st = await client.put(f"/seed-packets/{sid}/status", json={"status": "approved"}, headers=H)
    assert st.json()["status"] == "approved"


async def test_cross_tenant_forbidden(app_client):
    client, fake = app_client
    tid = (await client.post("/data-templates", json=data_template("t1"), headers=H)).json()["template_id"]
    other = await client.get(f"/data-templates/{tid}", headers={"x-tenant-id": "t2"})
    assert other.status_code == 403
