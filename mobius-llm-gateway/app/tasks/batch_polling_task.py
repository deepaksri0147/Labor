import asyncio
import time
import logging

from app.celery_app import celery_app as celery

logger = logging.getLogger(__name__)

_FAST_INTERVAL = 60          # poll every 60s for the first 2 hours
_SLOW_INTERVAL = 300         # poll every 300s after 2 hours
_FAST_PHASE = 2 * 3600       # 2 hours in seconds
_HARD_LIMIT = 24 * 3600      # 24 hours — Anthropic expires batch at this point


@celery.task(
    bind=True,
    name="batch.poll_and_fetch",
    queue="batch",
    max_retries=0,
    time_limit=_HARD_LIMIT + 120,
    soft_time_limit=_HARD_LIMIT + 60,
)
def batch_poll_and_fetch(
    self,
    batch_id: str,
    token: str,
    identity: dict,
    original_requests: list = None,
):
    from app.services.batch.anthropic_batch_service import AnthropicBatchService
    service = AnthropicBatchService()
    start_time = time.time()

    logger.info("batch.poll_and_fetch started | batch_id=%s", batch_id)

    while True:
        elapsed = time.time() - start_time

        # Hard limit — Anthropic will have expired the batch by now
        if elapsed >= _HARD_LIMIT:
            logger.error("batch.poll_and_fetch hard timeout | batch_id=%s elapsed=24h", batch_id)
            self.update_state(
                state="FAILURE",
                meta={"batch_id": batch_id, "stage": "expired", "elapsed_seconds": int(elapsed)},
            )
            return {"status": "expired", "batch_id": batch_id}

        # Poll Anthropic
        try:
            status = asyncio.run(service.poll(batch_id))
        except Exception as exc:
            logger.warning("poll error batch_id=%s error=%s — retrying after %ds", batch_id, exc, _FAST_INTERVAL)
            time.sleep(_FAST_INTERVAL)
            continue

        processing_status = status.get("processing_status")
        request_counts = status.get("request_counts", {})

        logger.info(
            "batch status | batch_id=%s status=%s elapsed=%ds counts=%s",
            batch_id, processing_status, int(elapsed), request_counts,
        )

        self.update_state(
            state="PROGRESS",
            meta={
                "batch_id": batch_id,
                "stage": "polling",
                "processing_status": processing_status,
                "elapsed_seconds": int(elapsed),
                "request_counts": request_counts,
            },
        )

        if processing_status == "ended":
            return _handle_ended(service, batch_id, token, identity, original_requests)

        # Adaptive sleep: fast phase first 2h, slow phase after
        interval = _FAST_INTERVAL if elapsed < _FAST_PHASE else _SLOW_INTERVAL
        logger.info("batch.poll_and_fetch sleeping %ds | batch_id=%s", interval, batch_id)
        time.sleep(interval)


def _handle_ended(service, batch_id, token, identity, original_requests):
    logger.info("batch ended — fetching results | batch_id=%s", batch_id)
    try:
        result = asyncio.run(
            service.fetch_results(batch_id, identity=identity, token=token)
        )
    except Exception as exc:
        logger.error("fetch failed | batch_id=%s error=%s", batch_id, exc)
        return {"status": "fetch_failed", "batch_id": batch_id, "error": str(exc)}

    errored = [
        r for r in result.get("results", [])
        if r.get("result", {}).get("type") == "errored"
    ]

    if errored and original_requests:
        errored_ids = {r.get("custom_id") for r in errored}
        retry_requests = [r for r in original_requests if r.get("custom_id") in errored_ids]
        if retry_requests:
            logger.info(
                "scheduling retry for %d errored results | batch_id=%s",
                len(retry_requests), batch_id,
            )
            celery.send_task(
                "batch.retry_errored",
                args=[retry_requests, token, identity],
                queue="batch",
            )

    logger.info(
        "batch.poll_and_fetch done | batch_id=%s total=%d errored=%d",
        batch_id, result.get("total", 0), len(errored),
    )
    return {
        "status": "completed",
        "batch_id": batch_id,
        "total": result.get("total", 0),
        "errored_count": len(errored),
    }


@celery.task(
    bind=True,
    name="batch.retry_errored",
    queue="batch",
    max_retries=3,
)
def batch_retry_errored(self, requests: list, token: str, identity: dict):
    from app.services.batch.anthropic_batch_service import AnthropicBatchService
    service = AnthropicBatchService()

    retry_num = self.request.retries
    backoff = 10 * (2 ** retry_num)  # 10s → 20s → 40s

    logger.info(
        "batch.retry_errored attempt=%d/%d requests=%d",
        retry_num + 1, self.max_retries + 1, len(requests),
    )

    try:
        result = asyncio.run(service.submit(requests, identity, token))
        new_batch_id = result.get("batch_id")
        logger.info("retry batch submitted | new_batch_id=%s", new_batch_id)

        # Poll the new batch — no original_requests to avoid infinite retry chains
        celery.send_task(
            "batch.poll_and_fetch",
            args=[new_batch_id, token, identity, None],
            queue="batch",
        )
        return {"status": "retried", "new_batch_id": new_batch_id}

    except Exception as exc:
        logger.warning(
            "batch.retry_errored failed attempt=%d error=%s — retrying in %ds",
            retry_num + 1, exc, backoff,
        )
        raise self.retry(exc=exc, countdown=backoff)
