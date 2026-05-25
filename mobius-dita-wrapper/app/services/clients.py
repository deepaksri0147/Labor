"""Clients for the three downstream surfaces DITA depends on.

1. RenderClient   -> the EXISTING synchronous DITA render engine (POST /dita). Unchanged.
2. LaborClient    -> the Labor Gateway, for call_llm chain steps and seed-packet to-llm.
3. BobClient      -> Bob-Service: ETL hydration (trigger/ml, etl/job-info, etl/stop-job)
                     and the Camunda workflow engine (trigger, status, wf import).

All shapes match the real specs (dita-service-openapi.json, api-docs_bobservice.json).
"""
from __future__ import annotations

from typing import Any

import httpx

from app.core.config import get_settings

_settings = get_settings()


class RenderClient:
    """Client for the existing Express DITA render service (dita-serviceexisting/dita).

    Real endpoints (see src/features/dita/routes.ts and src/features/*/routes.ts):
      POST  {api_prefix}/dita              — resolveMap: render bundle/chunks to html|text|pdf
      POST  {api_prefix}/dita/bob-llm      — resolveBobLlm: render then forward to Bob agent
      GET   {api_prefix}/bundles           — list bundles
      GET   {api_prefix}/bundles/{id}      — get bundle
      GET   {api_prefix}/bundles/{id}/chunks — list bundle chunks
      GET   {api_prefix}/chunks/{id}       — get chunk
      POST  {api_prefix}/acl/validate      — ACL validation
      GET   {api_prefix}/resolved-results  — historical resolve runs

    All routes are bearer-gated (attachPrincipal). We pass `render_auth_header` verbatim.

    The /dita route returns:
      - inline: binary (html/pdf) stream OR text/plain, with X-Dita-* headers
      - external sources (bqId/cohortId): application/json envelope
      - encrypted by default; pass ?encrypt=false for plaintext
    """

    def _url(self, path: str) -> str:
        return (
            _settings.render_base_url.rstrip("/")
            + _settings.render_api_prefix
            + path
        )

    def _headers(self, auth_header: str | None = None) -> dict[str, str]:
        h = {"Content-Type": "application/json"}
        token = auth_header or _settings.render_auth_header
        if token:
            h["Authorization"] = token if token.lower().startswith("bearer ") else f"Bearer {token}"
        return h

    async def render(
        self,
        *,
        bundle_id: str | None = None,
        chunk_ids: list[str] | None = None,
        data: dict[str, Any] | None = None,
        chunk_data: dict[str, Any] | None = None,
        fmt: str = "html",
        is_global: bool | None = None,
        pdf_options: dict[str, Any] | None = None,
        bq_id: str | None = None,
        bq_version: str | int | None = None,
        db_type: str | None = None,
        cohort_id: str | None = None,
        cohort_version: str | int | None = None,
        encrypt: bool = False,
        cleanup: bool = True,
        auth_header: str | None = None,
    ) -> dict[str, Any]:
        """POST {api_prefix}/dita — resolveMap.

        Body matches ResolveRequest (dita-serviceexisting/src/types/dita.types.ts).
        Plaintext mode (encrypt=false) returns the rendered content directly so the
        chain engine can persist it as an artifact.
        """
        body: dict[str, Any] = {"format": fmt}
        if bundle_id:
            body["bundle_id"] = bundle_id
        if chunk_ids:
            body["chunk_ids"] = chunk_ids
        if data:
            body["data"] = data
        if chunk_data:
            body["chunk_data"] = chunk_data
        if is_global is not None:
            body["is_global"] = is_global
        if pdf_options:
            body["pdfOptions"] = pdf_options
        if bq_id:
            body["bqId"] = bq_id
            if bq_version is not None:
                body["bqVersion"] = bq_version
            if db_type:
                body["dbType"] = db_type
        if cohort_id:
            body["cohortId"] = cohort_id
            if cohort_version is not None:
                body["cohortVersion"] = cohort_version

        params = {"encrypt": "false" if not encrypt else "true",
                  "cleanup": "true" if cleanup else "false"}

        url = self._url(_settings.render_dita_path)
        async with httpx.AsyncClient(timeout=_settings.render_timeout_seconds) as c:
            r = await c.post(url, json=body, headers=self._headers(auth_header), params=params)
            r.raise_for_status()
            ctype = r.headers.get("content-type", "")
            partial_errors = r.headers.get("x-dita-partial-errors")
            out: dict[str, Any] = {}
            if "application/json" in ctype:
                out = r.json() if isinstance(r.json(), dict) else {"results": r.json()}
            elif ctype.startswith("text/plain"):
                out = {"format": "text", "text_content": r.text, "content_type": ctype}
            elif ctype == "text/html" or ctype.startswith("text/html"):
                out = {"format": "html", "html_content": r.text, "content_type": ctype}
            elif ctype == "application/pdf":
                import base64
                out = {"format": "pdf", "pdf_base64": base64.b64encode(r.content).decode("ascii"),
                       "content_type": ctype}
            elif ctype == "application/octet-stream":
                # encrypted frame; surface bytes + crypto headers for downstream decryption
                import base64
                out = {
                    "format": fmt,
                    "encrypted_frame_b64": base64.b64encode(r.content).decode("ascii"),
                    "iv": r.headers.get("x-dita-encryption-iv"),
                    "auth_tag": r.headers.get("x-dita-encryption-auth-tag"),
                    "algorithm": r.headers.get("x-dita-encryption-algorithm"),
                    "plaintext_content_type": r.headers.get("x-dita-plaintext-content-type"),
                }
            else:
                out = {"content": r.text, "content_type": ctype}
            if partial_errors:
                out["partial_errors_count"] = int(partial_errors)
            return out

    async def render_bob_llm(
        self,
        *,
        agent_id: str,
        bundle_id: str | None = None,
        chunk_ids: list[str] | None = None,
        data: dict[str, Any] | None = None,
        chunk_data: dict[str, Any] | None = None,
        is_global: bool | None = None,
        pdf_options: dict[str, Any] | None = None,
        bq_id: str | None = None,
        cohort_id: str | None = None,
        user_id: str | None = None,
        session_id: str | None = None,
        refer_old_session: bool | None = None,
        edit: bool | None = None,
        regenerate: bool | None = None,
        streaming: bool | None = None,
        auth_header: str | None = None,
    ) -> dict[str, Any]:
        """POST {api_prefix}/dita/bob-llm — render + forward to Bob agent."""
        body: dict[str, Any] = {"agentId": agent_id}
        if bundle_id: body["bundle_id"] = bundle_id
        if chunk_ids: body["chunk_ids"] = chunk_ids
        if data: body["data"] = data
        if chunk_data: body["chunk_data"] = chunk_data
        if is_global is not None: body["is_global"] = is_global
        if pdf_options: body["pdfOptions"] = pdf_options
        if bq_id: body["bqId"] = bq_id
        if cohort_id: body["cohortId"] = cohort_id
        if user_id is not None: body["userId"] = user_id
        if session_id is not None: body["sessionId"] = session_id
        if refer_old_session is not None: body["referOldSession"] = refer_old_session
        if edit is not None: body["edit"] = edit
        if regenerate is not None: body["regenerate"] = regenerate
        if streaming is not None: body["streaming"] = streaming

        url = self._url(_settings.render_bob_llm_path)
        async with httpx.AsyncClient(timeout=_settings.render_timeout_seconds) as c:
            r = await c.post(url, json=body, headers=self._headers(auth_header))
            r.raise_for_status()
            return r.json()

    async def get_bundle(self, bundle_id: str, *, auth_header: str | None = None) -> dict[str, Any]:
        url = self._url(f"{_settings.render_bundles_path}/{bundle_id}")
        async with httpx.AsyncClient(timeout=_settings.render_timeout_seconds) as c:
            r = await c.get(url, headers=self._headers(auth_header))
            r.raise_for_status()
            return r.json()

    async def list_bundle_chunks(self, bundle_id: str, *, auth_header: str | None = None) -> dict[str, Any]:
        url = self._url(f"{_settings.render_bundles_path}/{bundle_id}/chunks")
        async with httpx.AsyncClient(timeout=_settings.render_timeout_seconds) as c:
            r = await c.get(url, headers=self._headers(auth_header))
            r.raise_for_status()
            return r.json()

    async def get_chunk(self, chunk_id: str, *, auth_header: str | None = None) -> dict[str, Any]:
        url = self._url(f"{_settings.render_chunks_path}/{chunk_id}")
        async with httpx.AsyncClient(timeout=_settings.render_timeout_seconds) as c:
            r = await c.get(url, headers=self._headers(auth_header))
            r.raise_for_status()
            return r.json()


class LaborClient:
    """Client for the Labor Gateway (see Labor/labor-gateway/app/api/*).

    Surfaces:
      Camunda push:
        POST  /camunda/labor/run-sync     run-to-terminal inline (short jobs)
        POST  /camunda/labor/run-async    enqueue + return {job_id, status}
        POST  /camunda/workflows/publish  register BPMN inside Bob
        GET   /camunda/labor/jobs/{job_id}/reconcile?pipeline_id=...

      Direct labor calls (long-form lifecycle):
        POST  /labor/calls                                  submit
        GET   /labor/calls/{labor_call_id}
        POST  /labor/calls/{labor_call_id}/validate
        POST  /labor/calls/{labor_call_id}/repair
        POST  /labor/calls/{labor_call_id}/revalidate

      Job lifecycle:
        GET   /labor/jobs/{job_id}
        POST  /labor/jobs/{job_id}/{cancel|retry|fork|pause|resume}
        GET   /labor/jobs/{job_id}/{artifacts|lineage|ux-state|events}
        POST  /labor/jobs/{job_id}/emit-event

      Batch:
        POST  /labor/batch
        GET   /labor/batch/{batch_id}
        GET   /labor/batch/{batch_id}/items
        POST  /labor/batch/{batch_id}/cancel

      Cache prefixes + executor registries.
    """

    def _headers(self, auth_header: str | None = None) -> dict[str, str]:
        h = {"Content-Type": "application/json"}
        token = auth_header or _settings.labor_auth_header
        if token:
            h["Authorization"] = token if token.lower().startswith("bearer ") else f"Bearer {token}"
        return h

    def _url(self, path: str) -> str:
        return _settings.labor_gateway_base_url.rstrip("/") + path

    # ---- Camunda push ----------------------------------------------------
    async def run_sync(self, labor_call: dict[str, Any], *, auth_header: str | None = None) -> dict[str, Any]:
        async with httpx.AsyncClient(timeout=_settings.labor_timeout_seconds) as c:
            r = await c.post(self._url(_settings.labor_run_sync_path), json=labor_call,
                             headers=self._headers(auth_header))
            r.raise_for_status()
            return r.json()

    async def run_async(self, labor_call: dict[str, Any], *, auth_header: str | None = None) -> dict[str, Any]:
        async with httpx.AsyncClient(timeout=_settings.labor_timeout_seconds) as c:
            r = await c.post(self._url(_settings.labor_run_async_path), json=labor_call,
                             headers=self._headers(auth_header))
            r.raise_for_status()
            return r.json()

    async def publish_workflow(self, dto: dict[str, Any], *, auth_header: str | None = None) -> dict[str, Any]:
        async with httpx.AsyncClient(timeout=_settings.labor_timeout_seconds) as c:
            r = await c.post(self._url(_settings.labor_workflows_publish_path), json=dto,
                             headers=self._headers(auth_header))
            r.raise_for_status()
            return r.json()

    async def reconcile_job(self, job_id: str, pipeline_id: str, *, auth_header: str | None = None) -> dict[str, Any]:
        async with httpx.AsyncClient(timeout=_settings.labor_timeout_seconds) as c:
            r = await c.get(self._url(f"{_settings.labor_reconcile_path}/{job_id}/reconcile"),
                            params={"pipeline_id": pipeline_id},
                            headers=self._headers(auth_header))
            r.raise_for_status()
            return r.json()

    # ---- Direct labor/calls ---------------------------------------------
    async def submit_call(self, labor_call: dict[str, Any], *, auth_header: str | None = None) -> dict[str, Any]:
        async with httpx.AsyncClient(timeout=_settings.labor_timeout_seconds) as c:
            r = await c.post(self._url(_settings.labor_calls_path), json=labor_call,
                             headers=self._headers(auth_header))
            r.raise_for_status()
            return r.json()

    async def get_call(self, labor_call_id: str, *, auth_header: str | None = None) -> dict[str, Any]:
        async with httpx.AsyncClient(timeout=_settings.labor_timeout_seconds) as c:
            r = await c.get(self._url(f"{_settings.labor_calls_path}/{labor_call_id}"),
                            headers=self._headers(auth_header))
            r.raise_for_status()
            return r.json()

    async def validate_call(self, labor_call_id: str, validation_req: dict[str, Any], *,
                            auth_header: str | None = None) -> dict[str, Any]:
        async with httpx.AsyncClient(timeout=_settings.labor_timeout_seconds) as c:
            r = await c.post(self._url(f"{_settings.labor_calls_path}/{labor_call_id}/validate"),
                             json=validation_req, headers=self._headers(auth_header))
            r.raise_for_status()
            return r.json()

    async def repair_call(self, labor_call_id: str, repair_req: dict[str, Any], *,
                          auth_header: str | None = None) -> dict[str, Any]:
        async with httpx.AsyncClient(timeout=_settings.labor_timeout_seconds) as c:
            r = await c.post(self._url(f"{_settings.labor_calls_path}/{labor_call_id}/repair"),
                             json=repair_req, headers=self._headers(auth_header))
            r.raise_for_status()
            return r.json()

    async def revalidate_call(self, labor_call_id: str, validation_req: dict[str, Any], *,
                              auth_header: str | None = None) -> dict[str, Any]:
        async with httpx.AsyncClient(timeout=_settings.labor_timeout_seconds) as c:
            r = await c.post(self._url(f"{_settings.labor_calls_path}/{labor_call_id}/revalidate"),
                             json=validation_req, headers=self._headers(auth_header))
            r.raise_for_status()
            return r.json()

    # ---- Job lifecycle --------------------------------------------------
    async def get_job(self, job_id: str, *, auth_header: str | None = None) -> dict[str, Any]:
        async with httpx.AsyncClient(timeout=_settings.labor_timeout_seconds) as c:
            r = await c.get(self._url(f"{_settings.labor_jobs_path}/{job_id}"),
                            headers=self._headers(auth_header))
            r.raise_for_status()
            return r.json()

    async def _job_action(self, job_id: str, action: str, body: dict[str, Any] | None = None,
                          *, auth_header: str | None = None) -> dict[str, Any]:
        async with httpx.AsyncClient(timeout=_settings.labor_timeout_seconds) as c:
            r = await c.post(self._url(f"{_settings.labor_jobs_path}/{job_id}/{action}"),
                             json=body or {}, headers=self._headers(auth_header))
            r.raise_for_status()
            return r.json()

    async def cancel_job(self, job_id: str, **kw): return await self._job_action(job_id, "cancel", **kw)
    async def retry_job(self, job_id: str, **kw): return await self._job_action(job_id, "retry", **kw)
    async def fork_job(self, job_id: str, **kw): return await self._job_action(job_id, "fork", **kw)
    async def pause_job(self, job_id: str, **kw): return await self._job_action(job_id, "pause", **kw)
    async def resume_job(self, job_id: str, **kw): return await self._job_action(job_id, "resume", **kw)

    async def job_artifacts(self, job_id: str, *, auth_header: str | None = None) -> dict[str, Any]:
        async with httpx.AsyncClient(timeout=_settings.labor_timeout_seconds) as c:
            r = await c.get(self._url(f"{_settings.labor_jobs_path}/{job_id}/artifacts"),
                            headers=self._headers(auth_header))
            r.raise_for_status()
            return r.json()

    async def job_lineage(self, job_id: str, *, auth_header: str | None = None) -> dict[str, Any]:
        async with httpx.AsyncClient(timeout=_settings.labor_timeout_seconds) as c:
            r = await c.get(self._url(f"{_settings.labor_jobs_path}/{job_id}/lineage"),
                            headers=self._headers(auth_header))
            r.raise_for_status()
            return r.json()

    async def job_ux_state(self, job_id: str, *, auth_header: str | None = None) -> dict[str, Any]:
        async with httpx.AsyncClient(timeout=_settings.labor_timeout_seconds) as c:
            r = await c.get(self._url(f"{_settings.labor_jobs_path}/{job_id}/ux-state"),
                            headers=self._headers(auth_header))
            r.raise_for_status()
            return r.json()

    async def emit_event(self, job_id: str, event: dict[str, Any], *,
                         auth_header: str | None = None) -> dict[str, Any]:
        async with httpx.AsyncClient(timeout=_settings.labor_timeout_seconds) as c:
            r = await c.post(self._url(f"{_settings.labor_jobs_path}/{job_id}/emit-event"),
                             json=event, headers=self._headers(auth_header))
            r.raise_for_status()
            return r.json()

    # ---- Batch ----------------------------------------------------------
    async def submit_batch(self, batch: dict[str, Any], *, auth_header: str | None = None) -> dict[str, Any]:
        async with httpx.AsyncClient(timeout=_settings.labor_timeout_seconds) as c:
            r = await c.post(self._url(_settings.labor_batch_path), json=batch,
                             headers=self._headers(auth_header))
            r.raise_for_status()
            return r.json()

    async def get_batch(self, batch_id: str, *, auth_header: str | None = None) -> dict[str, Any]:
        async with httpx.AsyncClient(timeout=_settings.labor_timeout_seconds) as c:
            r = await c.get(self._url(f"{_settings.labor_batch_path}/{batch_id}"),
                            headers=self._headers(auth_header))
            r.raise_for_status()
            return r.json()

    async def batch_items(self, batch_id: str, *, auth_header: str | None = None) -> dict[str, Any]:
        async with httpx.AsyncClient(timeout=_settings.labor_timeout_seconds) as c:
            r = await c.get(self._url(f"{_settings.labor_batch_path}/{batch_id}/items"),
                            headers=self._headers(auth_header))
            r.raise_for_status()
            return r.json()

    async def cancel_batch(self, batch_id: str, *, auth_header: str | None = None) -> dict[str, Any]:
        async with httpx.AsyncClient(timeout=_settings.labor_timeout_seconds) as c:
            r = await c.post(self._url(f"{_settings.labor_batch_path}/{batch_id}/cancel"),
                             headers=self._headers(auth_header))
            r.raise_for_status()
            return r.json()

    # ---- Cache prefixes -------------------------------------------------
    async def create_cache_prefix(self, body: dict[str, Any], *, auth_header: str | None = None) -> dict[str, Any]:
        async with httpx.AsyncClient(timeout=_settings.labor_timeout_seconds) as c:
            r = await c.post(self._url(_settings.labor_cache_prefix_path), json=body,
                             headers=self._headers(auth_header))
            r.raise_for_status()
            return r.json()

    async def get_cache_prefix(self, cache_prefix_id: str, *, auth_header: str | None = None) -> dict[str, Any]:
        async with httpx.AsyncClient(timeout=_settings.labor_timeout_seconds) as c:
            r = await c.get(self._url(f"{_settings.labor_cache_prefix_path}/{cache_prefix_id}"),
                            headers=self._headers(auth_header))
            r.raise_for_status()
            return r.json()

    # ---- Executor registries -------------------------------------------
    async def list_executors(self, *, auth_header: str | None = None) -> dict[str, Any]:
        async with httpx.AsyncClient(timeout=_settings.labor_timeout_seconds) as c:
            r = await c.get(self._url(_settings.labor_executors_path),
                            headers=self._headers(auth_header))
            r.raise_for_status()
            return r.json()

    async def create_executor_policy(self, body: dict[str, Any], *,
                                     auth_header: str | None = None) -> dict[str, Any]:
        async with httpx.AsyncClient(timeout=_settings.labor_timeout_seconds) as c:
            r = await c.post(self._url(_settings.labor_executor_policies_path), json=body,
                             headers=self._headers(auth_header))
            r.raise_for_status()
            return r.json()

    async def create_model_policy(self, body: dict[str, Any], *,
                                  auth_header: str | None = None) -> dict[str, Any]:
        async with httpx.AsyncClient(timeout=_settings.labor_timeout_seconds) as c:
            r = await c.post(self._url(_settings.labor_model_policies_path), json=body,
                             headers=self._headers(auth_header))
            r.raise_for_status()
            return r.json()

    async def get_model_policy(self, model_policy_id: str, *,
                               auth_header: str | None = None) -> dict[str, Any]:
        async with httpx.AsyncClient(timeout=_settings.labor_timeout_seconds) as c:
            r = await c.get(self._url(f"{_settings.labor_model_policies_path}/{model_policy_id}"),
                            headers=self._headers(auth_header))
            r.raise_for_status()
            return r.json()


class BobClient:
    """Bob-Service: ETL hydration + Camunda workflow engine."""

    # ---- ETL hydration ----------------------------------------------------
    async def trigger_etl(self, *, pipeline_id: str, namespace: str, payload: dict[str, Any]) -> dict[str, Any]:
        body = {
            "pipelineType": "ML",
            "pipelineId": pipeline_id,
            "namespace": namespace,
            "envConfig": {"jobMode": "async", "additionalFields": payload},
        }
        return await self._post("/v1.0/pipeline/trigger/ml", body)

    async def etl_job_info(self, job_id: str) -> dict[str, Any]:
        return await self._get(f"/v1.0/pipeline/etl/job-info/{job_id}")

    async def stop_etl_job(self, job_id: str) -> dict[str, Any]:
        return await self._post(f"/v1.0/pipeline/etl/stop-job/{job_id}", {})

    # ---- Camunda workflow engine -----------------------------------------
    async def pipeline_status(self, pipeline_id: str) -> dict[str, Any]:
        return await self._get(f"/v2.0/pipeline/status/{pipeline_id}")

    async def import_workflow(self, dto: dict[str, Any]) -> dict[str, Any]:
        return await self._post("/v1.0/wf/import", dto)

    async def fetch_and_lock(self, *, worker_id: str, topics: list[dict[str, Any]], max_tasks: int = 5) -> list[dict]:
        res = await self._post(
            "/engine-rest/external-task/fetchAndLock",
            {"workerId": worker_id, "maxTasks": max_tasks, "usePriority": True, "topics": topics},
        )
        return res if isinstance(res, list) else []

    async def complete_task(self, task_id: str, *, worker_id: str, variables: dict[str, Any]) -> None:
        await self._post(
            f"/engine-rest/external-task/{task_id}/complete",
            {"workerId": worker_id, "variables": _as_camunda_vars(variables)},
        )

    async def task_failure(self, task_id: str, *, worker_id: str, error_message: str, retries: int = 0) -> None:
        await self._post(
            f"/engine-rest/external-task/{task_id}/failure",
            {"workerId": worker_id, "errorMessage": error_message, "retries": retries, "retryTimeout": 60000},
        )

    # ---- low-level --------------------------------------------------------
    async def _post(self, path: str, body: dict[str, Any]) -> Any:
        async with httpx.AsyncClient(timeout=_settings.bob_timeout_seconds) as c:
            r = await c.post(_settings.bob_base_url.rstrip("/") + path, json=body)
            r.raise_for_status()
            return r.json() if r.content else {}

    async def _get(self, path: str) -> Any:
        async with httpx.AsyncClient(timeout=_settings.bob_timeout_seconds) as c:
            r = await c.get(_settings.bob_base_url.rstrip("/") + path)
            r.raise_for_status()
            return r.json() if r.content else {}


def _as_camunda_vars(d: dict[str, Any]) -> dict[str, Any]:
    import json

    out: dict[str, Any] = {}
    for k, v in d.items():
        if isinstance(v, bool):
            out[k] = {"value": v, "type": "Boolean"}
        elif isinstance(v, int):
            out[k] = {"value": v, "type": "Long"}
        elif isinstance(v, (dict, list)):
            out[k] = {"value": json.dumps(v), "type": "Json"}
        else:
            out[k] = {"value": v, "type": "String"}
    return out


# module singletons
render_client = RenderClient()
labor_client = LaborClient()
bob_client = BobClient()
