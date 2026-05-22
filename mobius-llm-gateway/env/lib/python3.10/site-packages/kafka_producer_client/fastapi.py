"""Optional FastAPI integration helpers.

Provides a ready-made lifespan context manager and dependency so you can
drop the producer into any FastAPI project with minimal boilerplate.
"""

from __future__ import annotations

import asyncio
from contextlib import asynccontextmanager
from typing import AsyncIterator

from .config import KafkaProducerConfig
from .producer import KafkaProducerClient

# Module-level singleton — set during lifespan
_client: KafkaProducerClient | None = None


def create_lifespan(config: KafkaProducerConfig):
    """Return a FastAPI lifespan context manager wired to the given config.

    Usage::

        from kafka_producer_client import KafkaProducerConfig
        from kafka_producer_client.fastapi import create_lifespan, get_producer

        config = KafkaProducerConfig(bootstrap_servers="broker:9092")
        app = FastAPI(lifespan=create_lifespan(config))

        @app.post("/events")
        async def post_event(producer=Depends(get_producer)):
            await producer.send_async({"foo": "bar"}, topic="events")
    """

    @asynccontextmanager
    async def lifespan(app) -> AsyncIterator[None]:  # noqa: ANN001
        global _client
        _client = KafkaProducerClient(config)
        _client.start_background_poll()
        try:
            yield
        finally:
            _client.close()
            _client = None

    return lifespan


def get_producer() -> KafkaProducerClient:
    """FastAPI dependency that returns the initialised producer.

    Usage::

        @app.post("/events")
        async def post_event(producer=Depends(get_producer)):
            ...
    """
    if _client is None:
        raise RuntimeError(
            "KafkaProducerClient not initialised. "
            "Did you pass create_lifespan() to FastAPI?"
        )
    return _client
