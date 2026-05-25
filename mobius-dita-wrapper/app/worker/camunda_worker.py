"""External-task worker for Bob/Camunda — DITA coarse steps.

Topics:
  dita.document_job  : run a whole document job as one task
  dita.chain_run     : run a whole prompt-chain as one task
"""
from __future__ import annotations

import asyncio
import json
import logging

from app.core.config import get_settings
from app.core.enums import JobState
from app.db.session import SessionLocal
from app.models import orm
from app.services import common
from app.services.chain_engine import run_chain
from app.services.clients import bob_client
from app.services.doc_jobs import run_document_job
from app.schemas.dita import DocumentJobRequest

_settings = get_settings()
logging.basicConfig(level=_settings.log_level)
log = logging.getLogger("dita.camunda_worker")


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


async def _handle_document_job(task: dict) -> None:
    payload = _var(task, "document_job_request")
    if payload is None:
        await bob_client.task_failure(task["id"], worker_id=_settings.bob_worker_id,
                                      error_message="missing document_job_request", retries=0)
        return
    req = DocumentJobRequest.model_validate(payload)
    job_id = common.new_id("djob")
    async with SessionLocal() as session:
        job = orm.DocumentJob(
            job_id=job_id, tenant_id=req.envelope.tenant_id,
            job_type=req.job_type if isinstance(req.job_type, str) else req.job_type.value,
            status=JobState.queued.value, request_payload=req.model_dump(mode="json"),
            trace_context=req.envelope.trace_context.model_dump(),
        )
        session.add(job)
        await session.flush()
        await run_document_job(session, job)
        await session.commit()
        result = {"job_id": job_id, "status": job.status,
                  "succeeded": JobState(job.status) == JobState.succeeded}
    await bob_client.complete_task(task["id"], worker_id=_settings.bob_worker_id, variables=result)


async def _handle_chain_run(task: dict) -> None:
    run_id = _var(task, "prompt_chain_run_id")
    if not run_id:
        await bob_client.task_failure(task["id"], worker_id=_settings.bob_worker_id,
                                      error_message="missing prompt_chain_run_id", retries=0)
        return
    async with SessionLocal() as session:
        run = await session.get(orm.PromptChainRun, run_id)
        if run is None:
            await bob_client.task_failure(task["id"], worker_id=_settings.bob_worker_id,
                                          error_message="chain run not found", retries=0)
            return
        await run_chain(session, run)
        await session.commit()
        result = {"prompt_chain_run_id": run_id, "status": run.status,
                  "succeeded": JobState(run.status) == JobState.succeeded}
    await bob_client.complete_task(task["id"], worker_id=_settings.bob_worker_id, variables=result)


HANDLERS = {
    _settings.topic_document_job: _handle_document_job,
    _settings.topic_chain_run: _handle_chain_run,
}


async def main() -> None:
    topics = [{"topicName": t, "lockDuration": _settings.external_task_lock_ms} for t in HANDLERS]
    log.info("dita camunda worker started; topics=%s", list(HANDLERS))
    while True:
        try:
            tasks = await bob_client.fetch_and_lock(
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
                await handler(task)
            except Exception as e:
                log.exception("task %s failed", task.get("id"))
                try:
                    await bob_client.task_failure(task["id"], worker_id=_settings.bob_worker_id,
                                                  error_message=str(e), retries=0)
                except Exception:
                    log.exception("failure report failed for %s", task.get("id"))


if __name__ == "__main__":
    asyncio.run(main())
