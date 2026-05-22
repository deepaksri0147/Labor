import asyncio
import json
import time
import logging
from collections import deque
from datetime import datetime
from statistics import mean
from typing import Any, Dict, Optional
from starlette.types import ASGIApp, Receive, Scope, Send

logger = logging.getLogger(__name__)

_SKIP_PATHS = frozenset({
    "/metrics", "/health",
    "/metrics/system", "/metrics/ollama/aggregate", "/metrics/flush-status",
})

_RPS_WINDOW_SEC      = 60.0
_LATENCY_BUF         = 1000
_AUTO_FLUSH_SEC      = 10.0
_MAX_FAILURE_REASONS = 50
_REQUEST_SAMPLE_LEN  = 500


def _percentile(sorted_data: list, p: int) -> float:
    n = len(sorted_data)
    if not n:
        return 0.0
    idx = (n - 1) * p / 100.0
    lo  = int(idx)
    hi  = min(lo + 1, n - 1)
    return round(sorted_data[lo] + (sorted_data[hi] - sorted_data[lo]) * (idx - lo), 2)


def _extract_tokens(body_bytes: bytes) -> tuple:
    """Return (prompt_tokens, completion_tokens) from a response body."""
    if not body_bytes:
        return 0, 0
    try:
        data = json.loads(body_bytes)
        payload = data.get("data", data) if isinstance(data, dict) else data
        if not isinstance(payload, dict):
            return 0, 0

        # OpenAI-style usage
        usage = payload.get("usage") or {}
        if usage:
            return int(usage.get("prompt_tokens") or 0), int(usage.get("completion_tokens") or 0)

        # Ollama wrapped: { tenantId, result: { status_code, result: { prompt_eval_count, eval_count } } }
        result = payload.get("result") or {}
        inner  = result.get("result") if isinstance(result, dict) else None
        if isinstance(inner, dict):
            return (
                int(inner.get("prompt_eval_count") or 0),
                int(inner.get("eval_count") or 0),
            )
    except Exception:
        pass
    return 0, 0


def _extract_model_tool(body_bytes: bytes) -> tuple:
    """Return (model_id, tool) from a request body."""
    if not body_bytes:
        return "", ""
    try:
        data = json.loads(body_bytes)
        if not isinstance(data, dict):
            return "", ""
        tool  = str(data.get("tool") or "")
        model = str(
            (data.get("input") or {}).get("model")
            or data.get("model")
            or ""
        )
        return model, tool
    except Exception:
        return "", ""


def _extract_failure_reason(resp_bytes: bytes, status_code: int) -> str:
    if not resp_bytes:
        return f"HTTP {status_code}"
    try:
        data   = json.loads(resp_bytes)
        detail = data.get("data") or data.get("detail") or data
        if isinstance(detail, dict):
            return str(detail.get("message") or detail.get("error") or status_code)[:200]
        return str(detail)[:200]
    except Exception:
        return f"HTTP {status_code}"


class MetricsMiddleware:
    """
    Pure ASGI metrics middleware.

    Captures per-endpoint stats (latency percentiles, RPS, tokens, failure reasons,
    model/tool info, request sample) and flushes them to PI every _AUTO_FLUSH_SEC seconds.
    """

    global_stats:      Dict[str, Dict[str, Any]] = {}
    _last_flushed:     Dict[str, float]           = {}
    _last_token:       Dict[str, str]             = {}
    _background_tasks: set                        = set()
    _session_id:       str = f"live_{int(time.time())}"
    _instance:         Optional[Any]              = None
    flush_results:     Dict[str, Dict[str, Any]] = {}

    def __init__(self, app: ASGIApp) -> None:
        self.app = app
        MetricsMiddleware._instance = self

    # ------------------------------------------------------------------ #
    # ASGI entry-point                                                     #
    # ------------------------------------------------------------------ #

    async def __call__(self, scope: Scope, receive: Receive, send: Send) -> None:
        if scope["type"] != "http":
            await self.app(scope, receive, send)
            return

        path   = scope.get("path", "")
        method = scope.get("method", "GET")

        if path in _SKIP_PATHS:
            await self.app(scope, receive, send)
            return

        token      = self._extract_token(scope)
        start_time = time.perf_counter()
        status_code = 500

        # ── capture request body ──────────────────────────────────────── #
        req_chunks: list = []

        async def capture_receive():
            message = await receive()
            if message["type"] == "http.request":
                req_chunks.append(message.get("body", b""))
            return message

        # ── capture response body (JSON / non-streaming only) ─────────── #
        resp_chunks: list = []
        resp_is_json = False

        async def send_wrapper(message):
            nonlocal status_code, resp_is_json
            if message["type"] == "http.response.start":
                status_code = message["status"]
                headers     = message.get("headers", [])
                ct = next(
                    (v.decode("latin1") for k, v in headers if k.lower() == b"content-type"),
                    "",
                )
                is_streaming = any(k.lower() == b"transfer-encoding" for k, v in headers)
                resp_is_json = "application/json" in ct and not is_streaming
            elif message["type"] == "http.response.body" and resp_is_json:
                resp_chunks.append(message.get("body", b""))
            await send(message)

        try:
            await self.app(scope, capture_receive, send_wrapper)
        except Exception:
            raise
        finally:
            duration_ms = (time.perf_counter() - start_time) * 1000

            req_body  = b"".join(req_chunks)
            resp_body = b"".join(resp_chunks)

            model_id, tool             = _extract_model_tool(req_body)
            prompt_tokens, comp_tokens = _extract_tokens(resp_body)

            request_sample = req_body.decode("utf-8", errors="ignore")[:_REQUEST_SAMPLE_LEN] if req_body else ""

            failure_reason = (
                _extract_failure_reason(resp_body, status_code)
                if status_code >= 400 else ""
            )

            key = self._record(
                method, path, status_code, duration_ms,
                model_id=model_id,
                tool=tool,
                prompt_tokens=prompt_tokens,
                completion_tokens=comp_tokens,
                failure_reason=failure_reason,
                request_sample=request_sample,
            )

            if token:
                MetricsMiddleware._last_token[key] = token
                now  = time.time()
                last = self._last_flushed.get(key, 0.0)
                if now - last >= _AUTO_FLUSH_SEC:
                    self._last_flushed[key] = now
                    task = asyncio.create_task(self._flush_to_pi(key, token))
                    MetricsMiddleware._background_tasks.add(task)
                    task.add_done_callback(MetricsMiddleware._background_tasks.discard)
            else:
                MetricsMiddleware.flush_results[key] = {
                    "status":    "skipped",
                    "reason":    "no Authorization header on request",
                    "endpoint":  path,
                    "timestamp": time.time(),
                }

    # ------------------------------------------------------------------ #
    # In-memory recording                                                  #
    # ------------------------------------------------------------------ #

    def _record(
        self,
        method: str,
        path: str,
        status_code: int,
        duration_ms: float,
        model_id: str = "",
        tool: str = "",
        prompt_tokens: int = 0,
        completion_tokens: int = 0,
        failure_reason: str = "",
        request_sample: str = "",
    ) -> str:
        key = f"{method} {path}"
        now = time.time()

        if key not in self.global_stats:
            self.global_stats[key] = {
                "total":             0,
                "success":           0,
                "failed":            0,
                "latencies":         deque(maxlen=_LATENCY_BUF),
                "timestamps":        deque(),
                "first_seen":        now,
                "failure_reasons":   deque(maxlen=_MAX_FAILURE_REASONS),
                "min_latency":       float("inf"),
                "max_latency":       0.0,
                "prompt_tokens":     0,
                "completion_tokens": 0,
                "total_tokens":      0,
                "token_duration_ms": 0.0,
                "token_req_count":   0,
                "model_id":          "",
                "tool":              "",
                "request_sample":    "",
            }

        s = self.global_stats[key]
        s["total"] += 1
        if 200 <= status_code < 400:
            s["success"] += 1
        else:
            s["failed"] += 1
            if failure_reason:
                s["failure_reasons"].append(failure_reason)

        s["latencies"].append(duration_ms)
        if duration_ms < s["min_latency"]:
            s["min_latency"] = duration_ms
        if duration_ms > s["max_latency"]:
            s["max_latency"] = duration_ms

        s["timestamps"].append(now)
        cutoff = now - _RPS_WINDOW_SEC
        while s["timestamps"] and s["timestamps"][0] < cutoff:
            s["timestamps"].popleft()

        if prompt_tokens or completion_tokens:
            s["prompt_tokens"]     += prompt_tokens
            s["completion_tokens"] += completion_tokens
            s["total_tokens"]      += prompt_tokens + completion_tokens
            s["token_duration_ms"] += duration_ms
            s["token_req_count"]   += 1

        if model_id:
            s["model_id"] = model_id
        if tool:
            s["tool"] = tool
        if request_sample:
            s["request_sample"] = request_sample

        return key

    # ------------------------------------------------------------------ #
    # Auto-flush to PI                                                     #
    # ------------------------------------------------------------------ #

    async def _flush_to_pi(self, key: str, token: str) -> None:
        started_at = time.time()
        try:
            from app.clients.pi_client import PIClient
            from app.core.settings import settings
            from app.services.metrics_collector import system_collector

            stats = self.global_stats.get(key)
            if not stats or stats["total"] == 0:
                return

            method, path = (key.split(" ", 1) + [""])[:2]
            latencies    = sorted(stats["latencies"])
            total        = stats["total"]
            success      = stats["success"]
            failed       = stats["failed"]
            now          = time.time()

            recent     = stats.get("timestamps", deque())
            window_sec = min(_RPS_WINDOW_SEC, now - stats.get("first_seen", now) + 1)
            rps        = round(len(recent) / window_sec, 2)

            system = system_collector.get_summary()

            # tokens_per_sec
            td_ms   = stats.get("token_duration_ms", 0.0)
            tok_sec = round(stats["total_tokens"] / (td_ms / 1000), 2) if td_ms > 0 else 0.0

            # failure_reasons — top 5 unique as JSON string
            reasons        = list(stats.get("failure_reasons", []))
            unique_reasons = list(dict.fromkeys(reasons))[:5]
            failure_reasons_str = json.dumps(unique_reasons) if unique_reasons else "[]"

            min_lat = stats.get("min_latency", 0.0)
            if min_lat == float("inf"):
                min_lat = 0.0
            max_lat = stats.get("max_latency", 0.0)

            instance = {
                # Original fields
                "test_id":           self._session_id,
                "scenario":          "live",
                "duration_sec":      int(now - stats["first_seen"]),
                "api_timestamp":     datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
                "endpoint":          path,
                "rps":               rps,
                "total_requests":    total,
                "success_rate":      round(success / total * 100, 2) if total else 100.0,
                "avg_latency":       round(mean(latencies), 2) if latencies else 0.0,
                "p50":               _percentile(latencies, 50),
                "p90":               _percentile(latencies, 90),
                "p95":               _percentile(latencies, 95),
                "p99":               _percentile(latencies, 99),
                "cpu_usage_pct":     system.get("cpu_avg",  0.0),
                "mem_usage_mb":      system.get("mem_avg",  0.0),
                "gpu_usage_pct":     system.get("gpu_avg",  0.0),
                "replica_count":     system.get("replicas", 1),
                # Phase 1: enhanced metrics
                "failure_count":     failed,
                "failure_reasons":   failure_reasons_str,
                "min_latency":       round(min_lat, 2),
                "max_latency":       round(max_lat, 2),
                # Phase 2: token metrics
                "prompt_tokens":     stats.get("prompt_tokens", 0),
                "completion_tokens": stats.get("completion_tokens", 0),
                "total_tokens":      stats.get("total_tokens", 0),
                "tokens_per_sec":    tok_sec,
                "model_id":          stats.get("model_id", ""),
                "tool":              stats.get("tool", ""),
                # Body storage
                "request_sample":    stats.get("request_sample", ""),
            }

            pi = PIClient(settings.PI_SCHEMA_ID_DEPLOYMENT)
            await pi.save_load_test_metrics(instance, token)

            MetricsMiddleware.flush_results[key] = {
                "status":              "success",
                "endpoint":            path,
                "total_requests":      total,
                "successful_requests": success,
                "failed_requests":     failed,
                "timestamp":           now,
                "elapsed_ms":          round((time.time() - started_at) * 1000, 1),
            }
            logger.info(
                "PI flush OK | endpoint=%s total=%d success=%d failed=%d rps=%.2f tokens=%d",
                path, total, success, failed, rps, stats.get("total_tokens", 0),
            )

        except Exception as exc:
            details = getattr(exc, "details", {})
            err_msg = (
                f"{exc} | pi_status={details.get('status_code')} "
                f"pi_response={details.get('response') or details.get('error')}"
                if details else str(exc)
            )
            MetricsMiddleware.flush_results[key] = {
                "status":     "error",
                "endpoint":   key,
                "error":      str(exc),
                "pi_details": details,
                "timestamp":  time.time(),
                "elapsed_ms": round((time.time() - started_at) * 1000, 1),
            }
            logger.error(
                "PI flush FAILED | endpoint=%s | %s",
                key, err_msg, exc_info=True,
            )

    # ------------------------------------------------------------------ #
    # Helpers                                                              #
    # ------------------------------------------------------------------ #

    @staticmethod
    def _extract_token(scope: Scope) -> Optional[str]:
        for name, value in scope.get("headers", []):
            if name.lower() == b"authorization":
                auth = value.decode("utf-8", errors="ignore")
                if auth.startswith("Bearer "):
                    return auth[7:].strip()
        return None
