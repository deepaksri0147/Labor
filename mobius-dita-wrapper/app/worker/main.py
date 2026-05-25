"""Worker: drains the document-job queue and the chain-run queue."""
from __future__ import annotations

import asyncio
import logging

from app.core.config import get_settings
from app.core.enums import JobState
from app.db.session import SessionLocal
from app.models import orm
from app.services import infra
from app.services.chain_engine import run_chain
from app.services.doc_jobs import run_document_job

logging.basicConfig(level=get_settings().log_level)
log = logging.getLogger("dita.worker")


async def _handle_doc_job(job_id: str) -> None:
    async with SessionLocal() as session:
        job = await session.get(orm.DocumentJob, job_id)
        if job is None:
            return
        if job.pause_requested or JobState(job.status) == JobState.paused:
            return
        if job.cancel_requested and JobState(job.status) != JobState.running:
            job.status = JobState.cancelled.value
            await session.commit()
            return
        try:
            await run_document_job(session, job)
            await session.commit()
        except Exception:
            log.exception("doc job %s failed", job_id)
            job.status = JobState.failed.value
            job.failure_reason = "worker exception"
            await session.commit()


async def _handle_chain_run(run_id: str) -> None:
    async with SessionLocal() as session:
        run = await session.get(orm.PromptChainRun, run_id)
        if run is None:
            return
        try:
            await run_chain(session, run)
            await session.commit()
        except Exception:
            log.exception("chain run %s failed", run_id)
            run.status = JobState.failed.value
            run.failure_reason = "worker exception"
            await session.commit()


async def main() -> None:
    from app.db.session import ensure_database_exists
    await ensure_database_exists()
    log.info("dita worker started")
    while True:
        job_id = await infra.dequeue_job(timeout=2)
        if job_id:
            await _handle_doc_job(job_id)
        run_id = await infra.dequeue_chain_run(timeout=2)
        if run_id:
            await _handle_chain_run(run_id)


if __name__ == "__main__":
    asyncio.run(main())
