import os
import logging
from kafka_producer_client import KafkaProducerConfig, configure_kafka_producer

logger = logging.getLogger(__name__)


def _patch_safe_serialize() -> None:
    """
    Patch kafka_producer_client._safe_serialize to recursively serialize
    dict values. The original returns dicts as-is, so Pydantic models or
    FastAPI Request objects nested inside a dict cause json.dumps to fail
    silently — action logs never reach Kafka.
    """
    try:
        from kafka_producer_client import action_logger as _al

        def _deep_serialize(obj):
            if obj is None or isinstance(obj, (str, int, float, bool)):
                return obj
            try:
                if hasattr(obj, "model_dump"):
                    return _deep_serialize(obj.model_dump())
                if hasattr(obj, "dict"):
                    return _deep_serialize(obj.dict())
                if isinstance(obj, dict):
                    return {k: _deep_serialize(v) for k, v in obj.items()}
                if isinstance(obj, (list, tuple)):
                    return [_deep_serialize(i) for i in obj]
                # Skip unserializable framework objects
                if type(obj).__name__ in ("Request", "Response", "BackgroundTasks"):
                    return f"<{type(obj).__name__}>"
                return str(obj)
            except Exception:
                return str(obj)

        _al._safe_serialize = _deep_serialize
        logger.info("kafka_producer_client._safe_serialize patched.")
    except Exception as e:
        logger.warning("Could not patch _safe_serialize: %s", e)


def setup_kafka_producer() -> None:
    """Configures the Kafka producer. Call during application startup."""
    bootstrap_servers = os.getenv("KAFKA_BOOTSTRAP_SERVERS", "localhost:9092")
    default_topic = os.getenv("KAFKA_ACTION_LOG_TOPIC", "action-logs-llm")
    client_id = os.getenv("KAFKA_CLIENT_ID", "sd-pipeline-producer")

    logger.info(
        "Configuring Kafka producer: servers=%s, topic=%s, client_id=%s",
        bootstrap_servers, default_topic, client_id
    )

    _patch_safe_serialize()

    try:
        configure_kafka_producer(KafkaProducerConfig(
            bootstrap_servers=bootstrap_servers,
            default_topic=default_topic,
            client_id=client_id,
        ))
        logger.info("Kafka producer successfully configured.")
    except Exception as e:
        logger.error("Failed to configure Kafka producer: %s", e, exc_info=True)
