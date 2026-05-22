"""External-task worker for Bob/Camunda.

Long-polls Bob's Camunda gateway for tasks on the topics this service owns, runs them
via the in-process orchestrator, and completes/fails them back. Run as its own process
(separate from the API and the Redis worker), or alongside the Redis worker.

Topics:
  - labor.execute   (coarse)  : the whole labor job as one atomic task. Default path.
  - labor.validate  (fine)    : validate a prior output; for human-in-the-loop workflows.
  - labor.repair    (fine)    : repair a prior output; for human-in-the-loop workflows.

The fine-grained topics are only consumed by workflows that explicitly route to them; a
coarse workflow never emits them, so there is zero per-stage overhead on the hot path.
"""
from __future__ import annotations

import asyncio
import json
import logging

from app.core.config import get_settings
from app.services.bob_camunda import BobCamundaClient
from app.services.camunda_bridge import run_labor_job_from_payload, run_to_terminal
from app.services.executors import seed_default_executors
from app.services.validation import validate_against_schema

_settings = get_settings()
logging.basicConfig(level=_settings.log_level)
log = logging.getLogger("labor.camunda_worker")


def _var(task: dict, name: str, default=None):
    v = (task.get("variables") or {}).get(name)
    if v is None:
        return default
    raw = v.get("value")
    if v.get("type") == "Json" and isinstance(raw, str):
        try:
            return json.loads(raw)
        except json.JSONDecodeError:
            return raw
    return raw


async def _handle_execute(client: BobCamundaClient, task: dict) -> None:
    payload = _var(task, "labor_call") or _var(task, "labor_call_request")
    if payload is None:
        await client.task_failure(task["id"], worker_id=_settings.bob_worker_id,
                                  error_message="missing labor_call variable", retries=0)
        return
    result = await run_labor_job_from_payload(payload)
    # complete the coarse task with the terminal result; Camunda branches on `succeeded`
    await client.complete_task(task["id"], worker_id=_settings.bob_worker_id, variables=result)


async def _handle_validate(client: BobCamundaClient, task: dict) -> None:
    parsed = _var(task, "parsed_output")
    schema = _var(task, "validation_schema")
    errors = validate_against_schema(parsed, schema) if isinstance(schema, dict) else []
    await client.complete_task(
        task["id"], worker_id=_settings.bob_worker_id,
        variables={"validation_status": "passed" if not errors else "failed",
                   "errors": [e.model_dump() for e in errors], "repair_required": bool(errors)},
    )


async def _handle_repair(client: BobCamundaClient, task: dict) -> None:
    # repair re-runs the existing job's loop; the job_id was carried on the process
    job_id = _var(task, "job_id")
    if not job_id:
        await client.task_failure(task["id"], worker_id=_settings.bob_worker_id,
                                  error_message="missing job_id for repair", retries=0)
        return
    result = await run_to_terminal(job_id)
    await client.complete_task(task["id"], worker_id=_settings.bob_worker_id, variables=result)


HANDLERS = {
    _settings.topic_labor_job: _handle_execute,
    _settings.topic_labor_validate: _handle_validate,
    _settings.topic_labor_repair: _handle_repair,
}


async def main() -> None:
    seed_default_executors()
    client = BobCamundaClient()
    topics = [
        {"topicName": t, "lockDuration": _settings.external_task_lock_ms}
        for t in HANDLERS
    ]
    log.info("camunda external-task worker started; topics=%s", list(HANDLERS))
    while True:
        try:
            tasks = await client.fetch_and_lock(
                worker_id=_settings.bob_worker_id, topics=topics, max_tasks=_settings.external_task_poll_max
            )
        except Exception:
            log.exception("fetchAndLock failed; backing off")
            await asyncio.sleep(2)
            continue
        if not tasks:
            await asyncio.sleep(0.5)
            continue
        for task in tasks:
            handler = HANDLERS.get(task.get("topicName", ""))
            if handler is None:
                continue
            try:
                await handler(client, task)
            except Exception as e:
                log.exception("task %s failed", task.get("id"))
                try:
                    await client.task_failure(task["id"], worker_id=_settings.bob_worker_id,
                                              error_message=str(e), retries=0)
                except Exception:
                    log.exception("failure report also failed for task %s", task.get("id"))


if __name__ == "__main__":
    asyncio.run(main())
