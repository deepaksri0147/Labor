"""Configuration for the Kafka producer client."""

from __future__ import annotations
from dataclasses import dataclass, field


@dataclass
class KafkaProducerConfig:
    """Configuration that consumers of this library pass in once.

    Usage::

        config = KafkaProducerConfig(
            bootstrap_servers="broker1:9092,broker2:9092",
            default_topic="my-events",
            acks="all",
        )
    """

    # --- Required ---
    bootstrap_servers: str = "localhost:9092"

    # --- Defaults you can override per-project ---
    client_id: str = "kafka-producer-client"
    default_topic: str | None = None
    acks: str = "all"
    retries: int = 3
    linger_ms: int = 5
    compression_type: str = "snappy"
    enable_idempotence: bool = True
    batch_size: int = 16_384          # 16 KB
    batch_num_messages: int = 100

    # --- Extra librdkafka config passthrough ---
    extra: dict[str, str] = field(default_factory=dict)

    def to_confluent_config(self) -> dict[str, str]:
        """Convert to the flat dict that confluent-kafka expects."""
        cfg: dict[str, str] = {
            "bootstrap.servers": self.bootstrap_servers,
            "client.id": self.client_id,
            "acks": self.acks,
            "retries": str(self.retries),
            "linger.ms": str(self.linger_ms),
            "compression.type": self.compression_type,
            "enable.idempotence": str(self.enable_idempotence).lower(),
            "batch.size": str(self.batch_size),
            "batch.num.messages": str(self.batch_num_messages),
        }
        cfg.update(self.extra)
        return cfg
