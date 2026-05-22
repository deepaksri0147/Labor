import json
import logging
import uuid
import asyncio
import random
from datetime import datetime, timezone
from typing import Any, Dict, List
from threading import Lock

import httpx
from fastapi import HTTPException

from app.clients.vault_client import get_llm_api_key
from app.clients.pi_client import PIClient
from app.core.settings import settings
from app.utils.utils import get_day_month, get_current_timestamp

logger = logging.getLogger(__name__)

ANTHROPIC_BASE_URL = "https://api.anthropic.com/v1"
ANTHROPIC_VERSION = "2023-06-01"
PI_SCHEMA_ID_DEPLOYMENT = settings.PI_SCHEMA_ID_DEPLOYMENT

_TRANSIENT_STATUS_CODES = {408, 409, 425, 429, 500, 502, 503, 504}

# Resilience defaults
_MAX_HTTP_RETRIES = settings.ANTHROPIC_BATCH_MAX_HTTP_RETRIES
_BACKOFF_BASE_SECONDS = settings.ANTHROPIC_BATCH_BACKOFF_BASE_SECONDS
_BACKOFF_MAX_SECONDS = settings.ANTHROPIC_BATCH_BACKOFF_MAX_SECONDS
_CIRCUIT_BREAKER_THRESHOLD = settings.ANTHROPIC_BATCH_CIRCUIT_BREAKER_THRESHOLD
_CIRCUIT_BREAKER_COOLDOWN_SECONDS = settings.ANTHROPIC_BATCH_CIRCUIT_BREAKER_COOLDOWN_SECONDS


def _headers() -> Dict[str, str]:
    return {
        "x-api-key": get_llm_api_key("anthropic"),
        "anthropic-version": ANTHROPIC_VERSION,
        "anthropic-beta": "prompt-caching-2024-07-31",
        "content-type": "application/json",
    }


class AnthropicBatchService:
    _cb_lock = Lock()
    _cb_failures = 0
    _cb_open_until = 0.0

    def __init__(self):
        self.pi_client = PIClient(PI_SCHEMA_ID_DEPLOYMENT)

    @staticmethod
    def _raise_api_error(
        *,
        status_code: int,
        error: str,
        message: str,
        details: Dict[str, Any] | None = None,
        retry_after_seconds: int | None = None,
    ):
        payload: Dict[str, Any] = {
            "error": error,
            "message": message,
        }
        if details is not None:
            payload["details"] = details
        if retry_after_seconds is not None:
            payload["retry_after_seconds"] = retry_after_seconds
        raise HTTPException(status_code=status_code, detail=payload)

    @classmethod
    def _before_request(cls):
        now = datetime.now(timezone.utc).timestamp()
        with cls._cb_lock:
            if cls._cb_open_until > now:
                retry_after = int(max(1, cls._cb_open_until - now))
                cls._raise_api_error(
                    status_code=503,
                    error="CIRCUIT_OPEN",
                    message="Anthropic batch API temporarily unavailable due to repeated upstream failures.",
                    retry_after_seconds=retry_after,
                )

    @classmethod
    def _mark_success(cls):
        with cls._cb_lock:
            cls._cb_failures = 0
            cls._cb_open_until = 0.0

    @classmethod
    def _mark_failure(cls):
        now = datetime.now(timezone.utc).timestamp()
        with cls._cb_lock:
            cls._cb_failures += 1
            if cls._cb_failures >= _CIRCUIT_BREAKER_THRESHOLD:
                cls._cb_open_until = now + _CIRCUIT_BREAKER_COOLDOWN_SECONDS

    @staticmethod
    def _is_transient_status(status_code: int) -> bool:
        return status_code in _TRANSIENT_STATUS_CODES

    async def _request_with_resilience(
        self,
        method: str,
        path: str,
        *,
        json_body: Dict[str, Any] | None = None,
        timeout_seconds: float = 30.0,
        expect_jsonl: bool = False,
    ) -> Any:
        self._before_request()

        last_error_text = ""
        for attempt in range(_MAX_HTTP_RETRIES + 1):
            try:
                async with httpx.AsyncClient(timeout=timeout_seconds) as client:
                    response = await client.request(
                        method=method,
                        url=f"{ANTHROPIC_BASE_URL}{path}",
                        headers=_headers(),
                        json=json_body,
                    )

                if response.status_code >= 400:
                    last_error_text = response.text
                    if response.status_code == 404:
                        self._mark_failure()
                        self._raise_api_error(
                            status_code=404,
                            error="BATCH_NOT_FOUND",
                            message="Batch resource was not found in Anthropic.",
                            details={"path": path, "response": response.text},
                        )
                    if self._is_transient_status(response.status_code) and attempt < _MAX_HTTP_RETRIES:
                        backoff = min(_BACKOFF_MAX_SECONDS, _BACKOFF_BASE_SECONDS * (2 ** attempt)) + random.uniform(0, 0.3)
                        logger.warning(
                            "Transient Anthropic error status=%s attempt=%s/%s path=%s retrying in %.2fs",
                            response.status_code,
                            attempt + 1,
                            _MAX_HTTP_RETRIES + 1,
                            path,
                            backoff,
                        )
                        await asyncio.sleep(backoff)
                        continue

                    self._mark_failure()
                    self._raise_api_error(
                        status_code=response.status_code,
                        error="ANTHROPIC_BATCH_ERROR",
                        message="Anthropic batch API returned an error.",
                        details={"response": response.text, "path": path},
                    )

                self._mark_success()
                if expect_jsonl:
                    return response.text
                if response.content:
                    return response.json()
                return {}

            except HTTPException:
                raise
            except Exception as exc:
                last_error_text = str(exc)
                if attempt < _MAX_HTTP_RETRIES:
                    backoff = min(_BACKOFF_MAX_SECONDS, _BACKOFF_BASE_SECONDS * (2 ** attempt)) + random.uniform(0, 0.3)
                    logger.warning(
                        "Anthropic request failed attempt=%s/%s path=%s error=%s retrying in %.2fs",
                        attempt + 1,
                        _MAX_HTTP_RETRIES + 1,
                        path,
                        exc,
                        backoff,
                    )
                    await asyncio.sleep(backoff)
                    continue

                self._mark_failure()
                logger.error("Anthropic request failed path=%s error=%s", path, exc)
                self._raise_api_error(
                    status_code=502,
                    error="ANTHROPIC_BATCH_UPSTREAM_UNAVAILABLE",
                    message="Unable to reach Anthropic batch API after retries.",
                    details={"path": path, "reason": str(exc)},
                )

        self._mark_failure()
        self._raise_api_error(
            status_code=502,
            error="ANTHROPIC_BATCH_UPSTREAM_UNAVAILABLE",
            message="Unable to reach Anthropic batch API after retries.",
            details={"path": path, "reason": last_error_text},
        )

    async def _log_batch(
        self,
        batch_id: str,
        request_count: int,
        identity: Dict[str, str],
        token: str,
    ):
        current_day, current_month = get_day_month()
        pi_payload = {
            "tool": "anthropic_batch",
            "inferid": str(uuid.uuid4()),
            "event_timestamp": get_current_timestamp(),
            "day": current_day,
            "month": current_month,
            "tenantid": identity.get("tenantId", "unknown").lower(),
            "userid": identity.get("userId", "unknown").lower(),
            "deployment_id": None,
            "agent_id": None,
            "model": "batch",
            "input_tokens": 0,
            "output_tokens": 0,
            "tokens": 0,
            "request": {"batch_id": batch_id, "request_count": request_count},
            "response": {},
            "duration_ms": 0,
            "service_name": "anthropic_batch",
        }
        try:
            await self.pi_client.save_inference_instance(pi_payload, token)
        except Exception as e:
            logger.error(f"Failed to log batch submit: {e}")

    async def submit(
        self,
        requests: List[Dict[str, Any]],
        identity: Dict[str, str],
        token: str,
    ) -> Dict[str, Any]:
        payload = {"requests": requests}
        data = await self._request_with_resilience(
            method="POST",
            path="/messages/batches",
            json_body=payload,
            timeout_seconds=30.0,
        )

        await self._log_batch(
            batch_id=data.get("id", ""),
            request_count=len(requests),
            identity=identity,
            token=token,
        )
        return {
            "batch_id": data.get("id"),
            "processing_status": data.get("processing_status"),
            "request_counts": data.get("request_counts", {}),
            "created_at": data.get("created_at"),
            "expires_at": data.get("expires_at"),
            "ended_at": data.get("ended_at"),
            "cancel_initiated_at": data.get("cancel_initiated_at"),
            "results_url": data.get("results_url"),
        }

    async def poll(self, batch_id: str) -> Dict[str, Any]:
        data = await self._request_with_resilience(
            method="GET",
            path=f"/messages/batches/{batch_id}",
            timeout_seconds=15.0,
        )

        return {
            "batch_id": data.get("id"),
            "processing_status": data.get("processing_status"),
            "request_counts": data.get("request_counts", {}),
            "created_at": data.get("created_at"),
            "expires_at": data.get("expires_at"),
            "ended_at": data.get("ended_at"),
            "cancel_initiated_at": data.get("cancel_initiated_at"),
            "results_url": data.get("results_url"),
        }

    async def cancel(self, batch_id: str) -> Dict[str, Any]:
        data = await self._request_with_resilience(
            method="POST",
            path=f"/messages/batches/{batch_id}/cancel",
            timeout_seconds=15.0,
        )

        return {
            "batch_id": data.get("id"),
            "processing_status": data.get("processing_status"),
            "request_counts": data.get("request_counts", {}),
            "created_at": data.get("created_at"),
            "expires_at": data.get("expires_at"),
            "ended_at": data.get("ended_at"),
            "cancel_initiated_at": data.get("cancel_initiated_at"),
        }

    async def delete(self, batch_id: str) -> Dict[str, Any]:
        # Anthropic requires the batch to be ended before deletion
        status = await self.poll(batch_id)
        if status["processing_status"] == "in_progress":
            raise HTTPException(
                status_code=409,
                detail={
                    "error": "BATCH_STILL_RUNNING",
                    "message": "Cancel the batch first before deleting. Call brick-llm-batch-cancel.",
                    "processing_status": status["processing_status"],
                },
            )
        await self._request_with_resilience(
            method="DELETE",
            path=f"/messages/batches/{batch_id}",
            timeout_seconds=15.0,
        )

        return {"batch_id": batch_id, "deleted": True}

    def _build_batch_result_rows(
        self,
        batch_id: str,
        results: list,
        identity: Dict[str, str],
        agent_id: str,
    ) -> list:
        now = datetime.now(timezone.utc)
        timestamp = get_current_timestamp()
        rows = []
        for item in results:
            result_block = item.get("result", {})
            result_type = result_block.get("type", "unknown")
            message = result_block.get("message", {})
            usage = message.get("usage", {})
            input_tokens = usage.get("input_tokens", 0)
            output_tokens = usage.get("output_tokens", 0)
            rows.append({
                "batch_id": batch_id,
                "custom_id": item.get("custom_id", ""),
                "result_type": result_type,
                "model": message.get("model", ""),
                "message_id": message.get("id", ""),
                "content": {"items": message.get("content", [])},
                "stop_reason": message.get("stop_reason", ""),
                "input_tokens": input_tokens,
                "output_tokens": output_tokens,
                "tokens": input_tokens + output_tokens,
                "tenantid": identity.get("tenantId", "unknown").lower(),
                "userid": identity.get("userId", "unknown").lower(),
                "agent_id": agent_id,
                "event_timestamp": timestamp,
                "day": now.day,
                "month": now.month,
            })
        return rows

    async def fetch_results(
        self,
        batch_id: str,
        identity: Dict[str, str],
        token: str,
        agent_id: str = None,
    ) -> Dict[str, Any]:
        # Check status first — results only available when ended
        status = await self.poll(batch_id)
        if status["processing_status"] != "ended":
            raise HTTPException(
                status_code=409,
                detail={
                    "error": "BATCH_NOT_ENDED",
                    "message": f"Batch is still '{status['processing_status']}'. Poll until processing_status is 'ended' before fetching results.",
                    "processing_status": status["processing_status"],
                    "request_counts": status["request_counts"],
                },
            )

        result_text = await self._request_with_resilience(
            method="GET",
            path=f"/messages/batches/{batch_id}/results",
            timeout_seconds=120.0,
            expect_jsonl=True,
        )
        results = [
            json.loads(line)
            for line in result_text.strip().splitlines()
            if line.strip()
        ]

        # Store each result row in the batch results PI schema
        rows = self._build_batch_result_rows(batch_id, results, identity, agent_id)
        if rows:
            try:
                await self.pi_client.save_batch_results(rows, token)
                logger.info("Stored %d batch result rows | batch_id=%s", len(rows), batch_id)
            except Exception as e:
                logger.error("Failed to store batch results: %s", e)

        usage_summary = self._aggregate_usage_summary(results)

        return {
            "batch_id": batch_id,
            "total": len(results),
            "results": results,
            "usage_summary": usage_summary,
        }

    @staticmethod
    def _aggregate_usage_summary(results: List[Dict[str, Any]]) -> Dict[str, int]:
        summary = {
            "total_input_tokens": 0,
            "total_output_tokens": 0,
            "total_tokens": 0,
            "total_cache_creation_tokens": 0,
            "total_cache_read_tokens": 0,
        }
        for item in results:
            usage = item.get("result", {}).get("message", {}).get("usage", {})
            input_tokens = int(usage.get("input_tokens", 0) or 0)
            output_tokens = int(usage.get("output_tokens", 0) or 0)
            cache_creation_tokens = int(usage.get("cache_creation_input_tokens", 0) or 0)
            cache_read_tokens = int(usage.get("cache_read_input_tokens", 0) or 0)

            summary["total_input_tokens"] += input_tokens
            summary["total_output_tokens"] += output_tokens
            summary["total_cache_creation_tokens"] += cache_creation_tokens
            summary["total_cache_read_tokens"] += cache_read_tokens

        summary["total_tokens"] = summary["total_input_tokens"] + summary["total_output_tokens"]
        return summary
