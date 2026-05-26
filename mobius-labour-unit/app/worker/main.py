"""Worker: long-running process that drains the Redis job queue and executes jobs.

Run as a separate container/replica from the API. Honors cooperative cancel/pause set
on the job row between enqueue and pickup.

Persistence delegated to the external data wrapper service.
"""
from __future__ import annotations

import asyncio
import logging

from app.core.config import get_settings
from app.core.enums import JobState
from app.services import wrapper_api, infra
from app.services.executors import seed_default_executors
from app.services.orchestrator import run_labor_job

logging.basicConfig(level=get_settings().log_level)
log = logging.getLogger("labor.worker")


async def handle(job_id: str, token: str | None = None) -> None:
    # Re-apply the bearer token that the originating API request carried, so the
    # worker's wrapper calls authenticate as the same user that submitted the job.
    wrapper_api.set_token(token)
    job = await wrapper_api.retrieve_one(wrapper_api.LABOR_JOB, {"job_id": job_id})
    if job is None:
        log.warning("job %s not found", job_id)
        return
    if JobState(job["status"]) == JobState.paused or job.get("pause_requested"):
        log.info("job %s paused; skipping", job_id)
        return
    if job.get("cancel_requested") and JobState(job["status"]) not in {JobState.running}:
        job["status"] = JobState.cancelled.value
        await wrapper_api.update(wrapper_api.LABOR_JOB, [job])
        return
    try:
        await run_labor_job(job)
    except Exception:
        log.exception("job %s failed unexpectedly", job_id)
        job["status"] = JobState.failed.value
        job["failure_reason"] = "worker exception"
        await wrapper_api.update(wrapper_api.LABOR_JOB, [job])


async def main() -> None:
    seed_default_executors()
    log.info("labor worker started")
    while True:
        item = await infra.dequeue_job(timeout=5)
        if item is None:
            continue
        job_id, token = item
        await handle(job_id, token)


if __name__ == "__main__":
    asyncio.run(main())
