import os
import socket
import logging
from opentelemetry import trace
from opentelemetry.sdk.resources import Resource, SERVICE_NAME
from opentelemetry.sdk.trace import TracerProvider
from opentelemetry.sdk.trace.export import BatchSpanProcessor
from opentelemetry.exporter.jaeger.thrift import JaegerExporter
from opentelemetry.instrumentation.fastapi import FastAPIInstrumentor
from opentelemetry.instrumentation.requests import RequestsInstrumentor
from opentelemetry.sdk.trace.sampling import TraceIdRatioBased
from fastapi import FastAPI
from app.core.config import Config

logger = logging.getLogger(__name__)


def _is_jaeger_reachable(host: str, port: int, timeout: float = 1.0) -> bool:
    try:
        with socket.create_connection((host, port), timeout=timeout):
            return True
    except OSError:
        return False


def setup_tracing(app: FastAPI):
    """OpenTelemetry Distributed Tracing Setup"""
    resource = Resource.create({SERVICE_NAME: Config.API_TITLE})
    provider = TracerProvider(
        resource=resource,
        sampler=TraceIdRatioBased(1.0),
    )
    trace.set_tracer_provider(provider)

    jaeger_url  = os.getenv("JAEGER_URL",  "localhost")
    jaeger_port = int(os.getenv("JAEGER_PORT", "14268"))
    collector_endpoint = f"http://{jaeger_url}:{jaeger_port}/api/traces"

    if _is_jaeger_reachable(jaeger_url, jaeger_port):
        jaeger_exporter = JaegerExporter(collector_endpoint=collector_endpoint)
        span_processor  = BatchSpanProcessor(
            jaeger_exporter,
            max_export_batch_size=10,
            schedule_delay_millis=5000,
        )
        provider.add_span_processor(span_processor)
        logger.info("Jaeger tracing enabled → %s", collector_endpoint)
    else:
        logger.warning("Jaeger not reachable at %s — tracing disabled", collector_endpoint)

    FastAPIInstrumentor.instrument_app(app)
    RequestsInstrumentor().instrument()

    return provider
