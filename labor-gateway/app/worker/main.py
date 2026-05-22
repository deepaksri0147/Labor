"""Worker: long-running process that drains the Redis job queue and executes jobs.

Run as a separate container/replica from the API. Honors cooperative cancel/pause set
on the job row between enqueue and pickup.
"""
from __future__ import annotations

import asyncio
import logging

from app.core.config import get_settings
from app.core.enums import JobState
from app.db.session import SessionLocal
from app.models import orm
from app.services import infra
from app.services.executors import seed_default_executors
from app.services.orchestrator import run_labor_job

logging.basicConfig(level=get_settings().log_level)
log = logging.getLogger("labor.worker")


async def handle(job_id: str) -> None:
    async with SessionLocal() as session:
        job = await session.get(orm.LaborJob, job_id)
        if job is None:
            log.warning("job %s not found", job_id)
            return
        if JobState(job.status) == JobState.paused or job.pause_requested:
            log.info("job %s paused; skipping", job_id)
            return
        if job.cancel_requested and JobState(job.status) not in {JobState.running}:
            job.status = JobState.cancelled.value
            await session.commit()
            return
        try:
            await run_labor_job(session, job)
            await session.commit()
        except Exception:
            log.exception("job %s failed unexpectedly", job_id)
            job.status = JobState.failed.value
            job.failure_reason = "worker exception"
            await session.commit()


async def main() -> None:
    seed_default_executors()
    log.info("labor worker started")
    while True:
        job_id = await infra.dequeue_job(timeout=5)
        if job_id is None:
            continue
        await handle(job_id)


if __name__ == "__main__":
    asyncio.run(main())
