import asyncio
import logging
import time
import httpx

from app.celery_app import celery_app as celery

logger = logging.getLogger(__name__)


@celery.task(
    bind=True,
    name="ollama.generate",
    queue="ollama",
    max_retries=2,
    default_retry_delay=5,
)
def ollama_generate_task(self, task_args: dict) -> dict:
    start_time = time.time()
    timings = {}

    try:
        request_data = task_args.get("request_data")
        token = task_args.get("token")
        identity = task_args.get("identity")

        # Stage 1 — init service
        step_start = time.time()
        self.update_state(state="PROCESSING", meta={
            "stage": "initializing",
            "message": "Initializing Ollama service...",
            "progress": 10,
            "timings": timings,
        })
        from app.services.inference.ollama_service import OllamaService
        service = OllamaService()
        timings["initialization"] = round(time.time() - step_start, 2)

        # Stage 2 — run inference
        step_start = time.time()
        self.update_state(state="PROCESSING", meta={
            "stage": "inference",
            "message": "Running Ollama generate...",
            "progress": 40,
            "timings": timings,
        })

        async def _run():
            try:
                return await service.generate(
                    request_data=request_data,
                    token=token,
                    identity=identity,
                )
            finally:
                await service.client.aclose()

        result = asyncio.run(_run())
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
            "ollama.finalize",
            args=[{
                "result": result,
                "request_data": request_data,
                "token": token,
                "identity": identity,
            }],
            queue="ollama",
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
        logger.info("ollama.generate completed in %ss | finalize_task=%s", total_duration, finalize_task.id)
        return {
            "status": "processing",
            "result": result,
            "stage": "inference_complete",
            "timings": timings,
            "total_duration": total_duration,
            "task_id": finalize_task.id,
        }

    except (httpx.TimeoutException, httpx.ConnectError) as exc:
        logger.warning("ollama.generate retrying: %s", exc)
        raise self.retry(exc=exc)
    except Exception as exc:
        total_duration = round(time.time() - start_time, 2)
        logger.error("ollama.generate failed: %s", exc)
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
    name="ollama.chat",
    queue="ollama",
    max_retries=2,
    default_retry_delay=5,
)
def ollama_chat_task(self, task_args: dict) -> dict:
    start_time = time.time()
    timings = {}

    try:
        request_data = task_args.get("request_data")
        token = task_args.get("token")
        identity = task_args.get("identity")

        # Stage 1 — init service
        step_start = time.time()
        self.update_state(state="PROCESSING", meta={
            "stage": "initializing",
            "message": "Initializing Ollama service...",
            "progress": 10,
            "timings": timings,
        })
        from app.services.inference.ollama_service import OllamaService
        service = OllamaService()
        timings["initialization"] = round(time.time() - step_start, 2)

        # Stage 2 — run inference
        step_start = time.time()
        self.update_state(state="PROCESSING", meta={
            "stage": "inference",
            "message": "Running Ollama chat...",
            "progress": 40,
            "timings": timings,
        })

        async def _run():
            try:
                return await service.chat(
                    request_data=request_data,
                    token=token,
                    identity=identity,
                )
            finally:
                await service.client.aclose()

        result = asyncio.run(_run())
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
            "ollama.finalize",
            args=[{
                "result": result,
                "request_data": request_data,
                "token": token,
                "identity": identity,
            }],
            queue="ollama",
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
        logger.info("ollama.chat completed in %ss | finalize_task=%s", total_duration, finalize_task.id)
        return {
            "status": "processing",
            "result": result,
            "stage": "inference_complete",
            "timings": timings,
            "total_duration": total_duration,
            "task_id": finalize_task.id,
        }

    except (httpx.TimeoutException, httpx.ConnectError) as exc:
        logger.warning("ollama.chat retrying: %s", exc)
        raise self.retry(exc=exc)
    except Exception as exc:
        total_duration = round(time.time() - start_time, 2)
        logger.error("ollama.chat failed: %s", exc)
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
    name="ollama.finalize",
    queue="ollama",
    max_retries=2,
    default_retry_delay=5,
)
def ollama_finalize_task(self, task_args: dict) -> dict:
    start_time = time.time()

    try:
        request_data = task_args.get("request_data", {})
        identity = task_args.get("identity", {})

        model = request_data.get("model", "unknown")
        status_code = task_args.get("result", {}).get("status_code")
        logger.info(
            "ollama.finalize | model=%s status=%s tenant=%s",
            model,
            status_code,
            identity.get("tenantId", "unknown"),
        )

        total_duration = round(time.time() - start_time, 2)
        return {
            "status": "finalized",
            "model": model,
            "total_duration": total_duration,
        }

    except Exception as exc:
        logger.error("ollama.finalize failed: %s", exc)
        raise self.retry(exc=exc)
