"""
Main LLM Gateway Application
OpenAI Compatible LLM Gateway using modular architecture
"""

# Configure logging before any other import that might log
from app.utils.logging_config import setup_logging
setup_logging()

import json as _json
import logging
import time
import httpx
from contextlib import asynccontextmanager
from fastapi import FastAPI, Request
from fastapi.responses import ORJSONResponse
from fastapi.middleware.gzip import GZipMiddleware
from starlette.types import ASGIApp, Receive, Scope, Send

logger = logging.getLogger(__name__)

from app.routes.v1.deployments import router as deployments_router
from app.routes.v1.unified_inference import router as unified_inference_router
from app.routes.v1.metrics import router as metrics_router
from app.routes.v1.adapter_list import router as adapter_list_router
from app.routes.v1.ollama import router as ollama_router
from app.routes.v1.vllm import router as vllm_router
from app.routes.v1.lorax import router as lorax_router
from app.routes.v1.cost import router as cost_router
from app.routes.v1.batch import router as batch_router
from app.routes.v1.vocabulary import router as vocabulary_router
from app.middleware.metrics_middleware import MetricsMiddleware
from app.services.metrics_collector import system_collector


from app.routes.v1.api_endpoints import health_check, root_info
from app.core.config import Config
from app.clients.vault_client import initialize_vault_kubernetes

_ENVELOPE_SKIP_PATHS = {"/metrics", "/docs", "/openapi.json", "/redoc"}


class ResponseEnvelopeMiddleware:
    """Wraps every JSON response in a consistent envelope:
      success → {"status": "success", "statusCode": N, "data": ...}
      error   → {"status": "error",   "statusCode": N, "message": "...", "details": ...}
    Streaming and non-JSON responses are passed through unchanged.
    """

    def __init__(self, app: ASGIApp):
        self.app = app

    async def __call__(self, scope: Scope, receive: Receive, send: Send):
        if scope["type"] != "http" or scope.get("path", "") in _ENVELOPE_SKIP_PATHS:
            await self.app(scope, receive, send)
            return

        status_code = 200
        raw_headers: list = []
        body_chunks: list[bytes] = []
        passthrough = False

        async def capture(message):
            nonlocal status_code, raw_headers, passthrough

            if message["type"] == "http.response.start":
                status_code = message["status"]
                raw_headers = list(message.get("headers", []))
                ct = next(
                    (v.decode("latin1") for k, v in raw_headers if k.lower() == b"content-type"),
                    "",
                )
                is_streaming = any(
                    k.lower() == b"transfer-encoding" for k, v in raw_headers
                )
                if is_streaming or "application/json" not in ct:
                    passthrough = True
                    await send(message)
                return

            if message["type"] == "http.response.body":
                if passthrough:
                    await send(message)
                    return

                body_chunks.append(message.get("body", b""))
                if message.get("more_body", False):
                    return

                body = b"".join(body_chunks)
                try:
                    data = _json.loads(body)
                    if status_code >= 400:
                        detail = data.get("detail", data) if isinstance(data, dict) else data
                        if isinstance(detail, dict):
                            msg = detail.get("message", detail.get("error", str(detail)))
                            envelope = {
                                "status": "error",
                                "statusCode": status_code,
                                "message": msg,
                                "details": detail,
                            }
                        else:
                            envelope = {
                                "status": "error",
                                "statusCode": status_code,
                                "message": str(detail),
                            }
                    else:
                        envelope = {
                            "status": "success",
                            "statusCode": status_code,
                            "data": data,
                        }
                    body = _json.dumps(envelope).encode()
                except Exception:
                    pass

                headers = [(k, v) for k, v in raw_headers if k.lower() != b"content-length"]
                headers.append((b"content-length", str(len(body)).encode()))
                await send({"type": "http.response.start", "status": status_code, "headers": headers})
                await send({"type": "http.response.body", "body": body, "more_body": False})

        await self.app(scope, receive, capture)


class TimingMiddleware:
    """Pure ASGI middleware for high-performance timing headers"""
    def __init__(self, app: ASGIApp):
        self.app = app

    async def __call__(self, scope: Scope, receive: Receive, send: Send):
        if scope["type"] != "http":
            await self.app(scope, receive, send)
            return

        start_time = time.perf_counter()

        async def send_wrapper(message):
            if message["type"] == "http.response.start":
                process_time = time.perf_counter() - start_time
                headers = list(message.get("headers", []))
                headers.append((b"X-Process-Time", str(process_time).encode()))
                message["headers"] = headers
            await send(message)

        await self.app(scope, receive, send_wrapper)

from prometheus_fastapi_instrumentator import Instrumentator
from mobius_error import register_exception_handlers

from app.core.tracing import setup_tracing

from app.core.kafka import setup_kafka_producer

@asynccontextmanager
async def lifespan(app: FastAPI):
    logger.info(f"Starting {Config.SERVICE_NAME} v{Config.API_VERSION}")

    initialize_vault_kubernetes()
    setup_kafka_producer()
    await system_collector.start()

    limits = httpx.Limits(max_keepalive_connections=20, max_connections=100)
    app.state.http_client = httpx.AsyncClient(limits=limits, timeout=Config.REQUEST_TIMEOUT)
    logger.info("HTTP client pool initialised | keepalive=20 max_connections=100")

    yield

    logger.info("Shutting down — closing HTTP client pool")
    await system_collector.stop()
    await app.state.http_client.aclose()

# Initialize FastAPI app with ORJSONResponse for faster serialization
app = FastAPI(
    title=Config.API_TITLE,
    description=Config.API_DESCRIPTION,
    version=Config.API_VERSION,
    default_response_class=ORJSONResponse,
    lifespan=lifespan
)

# 1. Prometheus Instrumentation
Instrumentator().instrument(app).expose(app)

# 2. OpenTelemetry Distributed Tracing Setup
setup_tracing(app)

# 3. Register Mobius Error Handlers
register_exception_handlers(app, app_name="llm-gateway")

# Add middlewares (innermost first — envelope wraps before gzip compresses)
app.add_middleware(ResponseEnvelopeMiddleware)
app.add_middleware(MetricsMiddleware)
app.add_middleware(GZipMiddleware, minimum_size=1000)
app.add_middleware(TimingMiddleware)

# OpenAI Compatible API Routes
app.include_router(deployments_router)
app.include_router(unified_inference_router)
app.include_router(metrics_router)
app.include_router(adapter_list_router)
app.include_router(ollama_router)
app.include_router(vllm_router)
app.include_router(lorax_router)
app.include_router(cost_router)
app.include_router(batch_router, prefix="/v1")
app.include_router(vocabulary_router)

@app.get("/health")
async def health():
    return await health_check()

@app.get("/")
async def root():
    return await root_info()

@app.get("/job/{job_id}", tags=["Queue"])
async def get_job_result(job_id: str):
    """Poll Celery task result by job_id returned from any inference endpoint."""
    from celery.result import AsyncResult
    from app.celery_app import celery_app
    result = AsyncResult(job_id, app=celery_app)
    response = {"job_id": job_id, "status": result.status}
    if result.successful():
        response["result"] = result.result
    elif result.failed():
        response["error"] = str(result.result)
    return response

