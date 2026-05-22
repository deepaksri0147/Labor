import asyncio
import logging
import time
import httpx

from app.celery_app import celery_app as celery

logger = logging.getLogger(__name__)


@celery.task(
    bind=True,
    name="inference.execute",
    queue="inference",
    max_retries=2,
    default_retry_delay=5,
)
def inference_execute_task(self, task_args: dict) -> dict:
    start_time = time.time()
    timings = {}

    try:
        tool = task_args.get("tool")
        tool_config = task_args.get("tool_config")
        user_input = task_args.get("user_input")
        token = task_args.get("token")
        identity = task_args.get("identity")

        # Stage 1 — init service
        step_start = time.time()
        self.update_state(state="PROCESSING", meta={
            "stage": "initializing",
            "message": f"Initializing inference service for tool={tool}...",
            "progress": 10,
            "timings": timings,
        })
        from app.services.inference.inference_unified import InferenceService
        service = InferenceService()
        timings["initialization"] = round(time.time() - step_start, 2)

        # Stage 2 — run inference
        step_start = time.time()
        self.update_state(state="PROCESSING", meta={
            "stage": "inference",
            "message": f"Running {tool} inference...",
            "progress": 40,
            "timings": timings,
        })

        result = asyncio.run(
            service.execute_inference(
                tool=tool,
                tool_config=tool_config,
                user_input=user_input,
                token=token,
                identity=identity,
            )
        )
        timings["inference"] = round(time.time() - step_start, 2)

        # Stage 3 — dispatch finalization
        step_start = time.time()
        self.update_state(state="PROCESSING", meta={
            "stage": "dispatching",
            "message": "Dispatching finalization task...",
            "progress": 80,
            "timings": timings,
        })
        finalize_task = self.app.send_task(
            "inference.finalize",
            args=[{
                "result": result,
                "tool": tool,
                "tool_config": tool_config,
                "identity": identity,
            }],
            queue="inference",
            expires=3600,
            retry=True,
            retry_policy={
                "max_retries": 3,
                "interval_start": 1,
                "interval_step": 2,
                "interval_max": 10,
            },
        )
        timings["dispatch"] = round(time.time() - step_start, 2)

        total_duration = round(time.time() - start_time, 2)
        logger.info("inference.execute completed in %ss | tool=%s finalize_task=%s", total_duration, tool, finalize_task.id)
        return {
            "status": "processing",
            "result": result,
            "stage": "inference_complete",
            "timings": timings,
            "total_duration": total_duration,
            "task_id": finalize_task.id,
        }

    except (httpx.TimeoutException, httpx.ConnectError) as exc:
        logger.warning("inference.execute retrying: %s", exc)
        raise self.retry(exc=exc)
    except Exception as exc:
        total_duration = round(time.time() - start_time, 2)
        logger.error("inference.execute failed: %s", exc)
        self.update_state(state="FAILURE", meta={
            "stage": "error",
            "message": str(exc),
            "error": str(exc),
            "timings": timings,
            "total_duration": total_duration,
        })
        raise


@celery.task(
    bind=True,
    name="inference.finalize",
    queue="inference",
    max_retries=2,
    default_retry_delay=5,
)
def inference_finalize_task(self, task_args: dict) -> dict:
    start_time = time.time()

    try:
        tool = task_args.get("tool", "unknown")
        identity = task_args.get("identity", {})
        endpoint = task_args.get("tool_config", {}).get("endpoint", "unknown")
        status_code = task_args.get("result", {}).get("status_code")

        logger.info(
            "inference.finalize | tool=%s endpoint=%s status=%s tenant=%s",
            tool,
            endpoint,
            status_code,
            identity.get("tenantId", "unknown"),
        )

        total_duration = round(time.time() - start_time, 2)
        return {
            "status": "finalized",
            "tool": tool,
            "endpoint": endpoint,
            "total_duration": total_duration,
        }

    except Exception as exc:
        logger.error("inference.finalize failed: %s", exc)
        raise self.retry(exc=exc)
