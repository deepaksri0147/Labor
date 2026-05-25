"""Client for Bob-Service / Bob-Camunda, wired to the real api-docs_bobservice shapes.

The labor gateway integrates with the platform workflow engine in three ways:

  1. As a COARSE step inside a Bob/Camunda workflow. Bob triggers the gateway (or the
     gateway's external-task worker claims a labor task), the gateway runs its fast
     in-process pipeline, and reports completion back. Camunda never sees the inner
     execute/parse/validate/repair loop.
  2. Optionally, FINE-GRAINED: for workflows that need a human between validate and
     repair, the gateway also exposes validate/repair as separate external-task topics.
  3. The gateway can publish/import the labor workflow definition (WorkflowPostDto) so the
     pipeline is "re-stitched" in Bob rather than hardcoded.

Endpoints (from api-docs_bobservice.json):
  POST /v1.0/pipeline/trigger/llm        PipelineTriggerRequest  -> trigger a run
  GET  /v2.0/pipeline/status/{id}        -> ProcessDefinition (run/process state)
  GET  /v1.0/wf/status/{wfId}            -> ProcessDefinition (workflow state)
  POST /v1.0/wf  /  /v1.0/wf/import      WorkflowPostDto         -> create/import workflow
  external-task fetchAndLock / complete  (Camunda REST, via Bob's camunda gateway)
"""
from __future__ import annotations

from typing import Any

import httpx

from app.core.config import get_settings

_settings = get_settings()


class BobCamundaClient:
    def __init__(self, base_url: str | None = None, timeout: float | None = None):
        self.base_url = (base_url or _settings.bob_base_url).rstrip("/")
        self.timeout = timeout or _settings.bob_timeout_seconds

    # ---- trigger a workflow run (coarse) ----------------------------------
    async def trigger_llm_pipeline(
        self,
        *,
        pipeline_id: str,
        namespace: str,
        llm_payload: dict[str, Any],
        version: int | None = None,
        deployed_version: int | None = None,
        job_mode: str = "async",
    ) -> dict[str, Any]:
        body = {
            "pipelineType": "LLM",
            "pipelineId": pipeline_id,
            "namespace": namespace,
            "llmPayload": llm_payload,
            "envConfig": {"jobMode": job_mode, "additionalFields": {}},
        }
        if version is not None:
            body["version"] = version
        if deployed_version is not None:
            body["deployedVersion"] = deployed_version
        return await self._post("/v1.0/pipeline/trigger/llm", body)

    # ---- status (projection source of truth for the outer process) -------
    async def pipeline_status(self, pipeline_id: str) -> dict[str, Any]:
        return await self._get(f"/v2.0/pipeline/status/{pipeline_id}")

    async def workflow_status(self, wf_id: str) -> dict[str, Any]:
        return await self._get(f"/v1.0/wf/status/{wf_id}")

    # ---- workflow definition (the "re-stitch" path) ----------------------
    async def import_workflow(self, dto: dict[str, Any]) -> dict[str, Any]:
        return await self._post("/v1.0/wf/import", dto)

    async def create_workflow(self, dto: dict[str, Any]) -> dict[str, Any]:
        return await self._post("/v1.0/wf", dto)

    # ---- external-task protocol (Camunda REST through Bob) ---------------
    async def fetch_and_lock(
        self, *, worker_id: str, topics: list[dict[str, Any]], max_tasks: int = 5
    ) -> list[dict[str, Any]]:
        body = {"workerId": worker_id, "maxTasks": max_tasks, "usePriority": True, "topics": topics}
        res = await self._post("/engine-rest/external-task/fetchAndLock", body)
        return res if isinstance(res, list) else []

    async def complete_task(self, task_id: str, *, worker_id: str, variables: dict[str, Any]) -> None:
        await self._post(
            f"/engine-rest/external-task/{task_id}/complete",
            {"workerId": worker_id, "variables": _as_camunda_vars(variables)},
        )

    async def task_failure(
        self, task_id: str, *, worker_id: str, error_message: str, retries: int = 0, retry_timeout_ms: int = 60000
    ) -> None:
        await self._post(
            f"/engine-rest/external-task/{task_id}/failure",
            {
                "workerId": worker_id,
                "errorMessage": error_message,
                "retries": retries,
                "retryTimeout": retry_timeout_ms,
            },
        )

    # ---- low-level --------------------------------------------------------
    async def _post(self, path: str, body: dict[str, Any]) -> Any:
        async with httpx.AsyncClient(timeout=self.timeout) as c:
            r = await c.post(self.base_url + path, json=body)
            r.raise_for_status()
            return r.json() if r.content else {}

    async def _get(self, path: str) -> Any:
        async with httpx.AsyncClient(timeout=self.timeout) as c:
            r = await c.get(self.base_url + path)
            r.raise_for_status()
            return r.json() if r.content else {}


def _as_camunda_vars(d: dict[str, Any]) -> dict[str, Any]:
    """Wrap plain values in Camunda's {value, type} variable envelope."""
    out: dict[str, Any] = {}
    for k, v in d.items():
        if isinstance(v, bool):
            out[k] = {"value": v, "type": "Boolean"}
        elif isinstance(v, int):
            out[k] = {"value": v, "type": "Long"}
        elif isinstance(v, (dict, list)):
            import json

            out[k] = {"value": json.dumps(v), "type": "Json"}
        else:
            out[k] = {"value": v, "type": "String"}
    return out


def map_process_state_to_jobstate(process: dict[str, Any]) -> str | None:
    """Project a Bob ProcessDefinition status onto our JobState vocabulary.

    Defensive: the ProcessDefinition shape varies; we read common fields and fall back to
    None (meaning 'keep the gateway's own status') when we can't map confidently.
    """
    state = (process.get("state") or process.get("status") or "").lower()
    mapping = {
        "active": "running",
        "running": "running",
        "completed": "succeeded",
        "complete": "succeeded",
        "externally_terminated": "cancelled",
        "terminated": "cancelled",
        "internally_terminated": "failed",
        "suspended": "paused",
        "failed": "failed",
        "incident": "failed",
    }
    return mapping.get(state)
