"""Core Kafka producer with synchronous and asynchronous interfaces."""

from __future__ import annotations

import asyncio
import json
import logging
import threading
from typing import Any

from confluent_kafka import Producer as ConfluentProducer

from .config import KafkaProducerConfig

logger = logging.getLogger(__name__)


class KafkaProducerClient:
    """Reusable Kafka producer — works in sync scripts *and* async frameworks.

    Usage (sync)::

        from kafka_producer_client import KafkaProducerClient, KafkaProducerConfig

        client = KafkaProducerClient(KafkaProducerConfig(
            bootstrap_servers="broker:9092",
            default_topic="events",
        ))
        client.send({"user": "alice", "action": "login"})
        client.flush()

    Usage (async)::

        client = KafkaProducerClient(config)
        await client.send_async("events", {"user": "alice"})
        client.close()
    """

    def __init__(self, config: KafkaProducerConfig) -> None:
        self._config = config
        self._producer = ConfluentProducer(config.to_confluent_config())
        self._poll_thread: threading.Thread | None = None
        self._poll_stop = threading.Event()

    # ------------------------------------------------------------------
    # Lifecycle
    # ------------------------------------------------------------------

    def start_background_poll(self, interval: float = 0.1) -> None:
        """Start a daemon thread that continuously polls the producer so
        delivery callbacks fire.  Useful inside long-running services."""
        if self._poll_thread and self._poll_thread.is_alive():
            return

        self._poll_stop.clear()

        def _poll_loop() -> None:
            while not self._poll_stop.is_set():
                self._producer.poll(0)
                self._poll_stop.wait(interval)

        self._poll_thread = threading.Thread(target=_poll_loop, daemon=True)
        self._poll_thread.start()
        logger.info("Background poll thread started (interval=%.2fs)", interval)

    def stop_background_poll(self) -> None:
        """Signal the background poll thread to stop."""
        self._poll_stop.set()
        if self._poll_thread:
            self._poll_thread.join(timeout=5)
            self._poll_thread = None

    def flush(self, timeout: float = 10.0) -> int:
        """Block until all outstanding messages are delivered or *timeout*
        seconds elapse.  Returns the number of messages still in the queue."""
        return self._producer.flush(timeout=timeout)

    def close(self) -> None:
        """Flush remaining messages and stop the poll thread."""
        self.flush()
        self.stop_background_poll()

    @property
    def queue_length(self) -> int:
        return len(self._producer)

    # ------------------------------------------------------------------
    # Synchronous send
    # ------------------------------------------------------------------

    def send(
        self,
        value: dict[str, Any],
        *,
        topic: str | None = None,
        key: str | None = None,
        headers: dict[str, str] | None = None,
        on_delivery: Any | None = None,
    ) -> None:
        """Fire-and-forget produce.  The optional *on_delivery* callback has
        the signature ``(err, msg) -> None``."""
        resolved_topic = topic or self._config.default_topic
        if not resolved_topic:
            raise ValueError(
                "No topic provided and no default_topic in config."
            )

        kafka_headers = (
            [(k, v.encode()) for k, v in headers.items()] if headers else None
        )

        self._producer.produce(
            topic=resolved_topic,
            value=json.dumps(value).encode("utf-8"),
            key=key.encode("utf-8") if key else None,
            headers=kafka_headers,
            callback=on_delivery or self._default_callback,
        )
        self._producer.poll(0)

    def send_batch(
        self,
        events: list[dict[str, Any]],
        *,
        topic: str | None = None,
        key: str | None = None,
        headers: dict[str, str] | None = None,
    ) -> int:
        """Produce many messages.  Returns the count enqueued."""
        for event in events:
            self.send(value=event, topic=topic, key=key, headers=headers)
        return len(events)

    # ------------------------------------------------------------------
    # Async send (for FastAPI / asyncio services)
    # ------------------------------------------------------------------

    async def send_async(
        self,
        value: dict[str, Any],
        *,
        topic: str | None = None,
        key: str | None = None,
        headers: dict[str, str] | None = None,
    ) -> dict[str, Any]:
        """Await the broker acknowledgement without blocking the event loop.

        Returns ``{"topic": ..., "partition": ..., "offset": ...}``."""
        resolved_topic = topic or self._config.default_topic
        if not resolved_topic:
            raise ValueError(
                "No topic provided and no default_topic in config."
            )

        loop = asyncio.get_running_loop()
        fut: asyncio.Future[dict[str, Any]] = loop.create_future()

        def _ack(err, msg):
            if err:
                loop.call_soon_threadsafe(
                    fut.set_exception, Exception(str(err))
                )
            else:
                loop.call_soon_threadsafe(
                    fut.set_result,
                    {
                        "topic": msg.topic(),
                        "partition": msg.partition(),
                        "offset": msg.offset(),
                    },
                )

        kafka_headers = (
            [(k, v.encode()) for k, v in headers.items()] if headers else None
        )

        self._producer.produce(
            topic=resolved_topic,
            value=json.dumps(value).encode("utf-8"),
            key=key.encode("utf-8") if key else None,
            headers=kafka_headers,
            callback=_ack,
        )
        self._producer.poll(0)

        return await fut

    async def send_batch_async(
        self,
        events: list[dict[str, Any]],
        *,
        topic: str | None = None,
        key: str | None = None,
        headers: dict[str, str] | None = None,
    ) -> list[dict[str, Any]]:
        """Send many events concurrently.  Returns list of offset metadata."""
        tasks = [
            self.send_async(value=event, topic=topic, key=key, headers=headers)
            for event in events
        ]
        return await asyncio.gather(*tasks)

    # ------------------------------------------------------------------
    # Internal
    # ------------------------------------------------------------------

    @staticmethod
    def _default_callback(err, msg) -> None:
        if err:
            logger.error("DELIVERY FAILED [%s]: %s", msg.topic(), err)
        else:
            logger.debug(
                "Delivered to %s [%d] @ offset %d",
                msg.topic(),
                msg.partition(),
                msg.offset(),
            )
