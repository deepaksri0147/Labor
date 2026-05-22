"""Tests for the Camunda integration paths (coarse step)."""
from __future__ import annotations

import pytest

from tests.conftest import labor_call

pytestmark = pytest.mark.asyncio


async def test_camunda_run_sync_coarse(app_client):
    """The whole labor job runs as one synchronous coarse step and returns a terminal
    result Camunda can branch on."""
    client, stub, fake = app_client
    r = await client.post("/camunda/labor/run-sync", json=labor_call(idem="cz"),
                          headers={"x-tenant-id": "t1"})
    assert r.status_code == 200
    body = r.json()
    assert body["status"] == "succeeded"
    assert body["succeeded"] is True
    assert body["parsed_output_ref"]  # an artifact was produced


async def test_external_task_execute_handler(app_client, monkeypatch):
    """The labor.execute external-task handler runs the job and completes the task with
    the terminal result variables."""
    client, stub, fake = app_client
    from app.worker import camunda_worker

    completed = {}

    class FakeClient:
        async def complete_task(self, task_id, *, worker_id, variables):
            completed["task_id"] = task_id
            completed["variables"] = variables

        async def task_failure(self, *a, **k):
            completed["failed"] = True

    task = {
        "id": "ext-1",
        "topicName": "labor.execute",
        "variables": {"labor_call": {"type": "Json", "value": __import__("json").dumps(labor_call(idem="ext"))}},
    }
    await camunda_worker._handle_execute(FakeClient(), task)
    assert completed.get("task_id") == "ext-1"
    assert completed["variables"]["succeeded"] is True
    assert "failed" not in completed
