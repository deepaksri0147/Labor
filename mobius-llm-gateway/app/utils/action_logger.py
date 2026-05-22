import json
import logging
from datetime import datetime, timezone
from typing import Any, Dict, Optional

logger = logging.getLogger(__name__)


def _serialize(obj: Any) -> Any:
    """Recursively convert any object to a JSON-safe structure."""
    if obj is None or isinstance(obj, (str, int, float, bool)):
        return obj
    if hasattr(obj, "model_dump"):
        return _serialize(obj.model_dump())
    if hasattr(obj, "dict"):
        return _serialize(obj.dict())
    if isinstance(obj, dict):
        return {k: _serialize(v) for k, v in obj.items()}
    if isinstance(obj, (list, tuple)):
        return [_serialize(i) for i in obj]
    # Skip Starlette/FastAPI Request objects — not serializable and not useful
    if type(obj).__name__ == "Request":
        return None
    try:
        json.dumps(obj)
        return obj
    except (TypeError, ValueError):
        return str(obj)


def send_action_log(
    *,
    action_type: str,
    node_type: str,
    endpoint: str,
    identity: Dict[str, str],
    request_data: Any = None,
    response_data: Any = None,
    status: str,
    error: Optional[str] = None,
    duration_ms: Optional[int] = None,
    tool: Optional[str] = None,
) -> None:
    try:
        from kafka_producer_client import get_kafka_producer
        payload = {
            "actionType": action_type,
            "nodeType": node_type,
            "nodeId": endpoint,
            "tenantId": identity.get("tenantId", "unknown"),
            "userId": identity.get("userId", "unknown"),
            "status": status,
            "tool": tool,
            "endpoint": endpoint,
            "timestamp": datetime.now(timezone.utc).isoformat(),
            "request": _serialize(request_data),
            "response": _serialize(response_data),
            "error": error,
            "duration_ms": duration_ms,
        }
        get_kafka_producer().send(payload)
        logger.info("Action log sent | endpoint=%s status=%s tool=%s", endpoint, status, tool)
    except Exception as e:
        logger.error("Failed to send action log: %s", e, exc_info=True)
