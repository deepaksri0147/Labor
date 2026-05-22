import asyncio
import logging
import time
from datetime import datetime
from typing import Any, Dict, List, Optional

import httpx

from app.middleware.metrics_middleware import MetricsMiddleware
from app.services.metrics_persistence import metrics_persistence
from app.services.metrics_registry import MetricsRegistry

logger = logging.getLogger(__name__)


class LoadGeneratorService:
    """
    Python-native async load generator — useful when k6 is unavailable or
    for quick smoke tests triggered via the API itself.

    Pacing: tokens are issued at `target_rps` per second using a semaphore
    + an asyncio.sleep-based rate limiter, avoiding request pile-up.
    """

    async def run_load_test(
        self,
        target_url:   str,
        token:        str,
        payload:      Dict[str, Any],
        duration_sec: int = 30,
        target_rps:   int = 5,
        scenario:     str = "baseline",
        test_id:      Optional[str] = None,
    ) -> Dict[str, Any]:

        test_id = test_id or f"native_{int(time.time())}"
        logger.info(
            "Load test [%s] start | url=%s duration=%ds rps=%d",
            test_id, target_url, duration_sec, target_rps,
        )

        bearer = token if token.startswith("Bearer ") else f"Bearer {token}"
        headers = {
            "Content-Type":   "application/json",
            "Authorization":  bearer,
            "X-Test-Scenario": scenario,
        }

        results: List[Dict[str, Any]] = []
        test_start = time.time()
        interval   = 1.0 / max(target_rps, 1)

        async def _request(client: httpx.AsyncClient, seq: int) -> Dict[str, Any]:
            t0 = time.perf_counter()
            try:
                resp = await client.post(target_url, json=payload, headers=headers, timeout=60.0)
                return {"status": resp.status_code, "duration_ms": (time.perf_counter() - t0) * 1000}
            except Exception as exc:
                return {"status": 0, "duration_ms": (time.perf_counter() - t0) * 1000, "error": str(exc)}

        async with httpx.AsyncClient() as client:
            seq = 0
            while time.time() - test_start < duration_sec:
                task_start = time.time()
                asyncio.create_task(_request(client, seq)).add_done_callback(
                    lambda f: results.append(f.result()) if not f.cancelled() else None
                )
                seq += 1
                # Pace requests: sleep for the remainder of the interval slot
                elapsed = time.time() - task_start
                await asyncio.sleep(max(0.0, interval - elapsed))

            # Allow in-flight requests up to 30 s to finish
            deadline = time.time() + 30
            while len(results) < seq and time.time() < deadline:
                await asyncio.sleep(0.1)

        total_requests = len(results)
        logger.info("Load test [%s] finished — %d responses collected", test_id, total_requests)

        # ── Aggregate results ─────────────────────────────────────────
        successes  = [r for r in results if 200 <= r["status"] < 400]
        failures   = [r for r in results if r["status"] < 200 or r["status"] >= 400]
        latencies  = sorted(r["duration_ms"] for r in results)
        n          = len(latencies)

        def _pct(p: int) -> float:
            if not latencies:
                return 0.0
            idx = (n - 1) * p / 100.0
            lo  = int(idx)
            hi  = min(lo + 1, n - 1)
            return round(latencies[lo] + (latencies[hi] - latencies[lo]) * (idx - lo), 2)

        avg_lat      = round(sum(latencies) / n, 2) if latencies else 0.0
        success_rate = round(len(successes) / max(total_requests, 1) * 100, 2)
        actual_rps   = round(total_requests / max(duration_sec, 1), 2)

        # Build a report using the middleware's live stats first, then overwrite
        # the entry for this specific URL with exact data from the run.
        registry = MetricsRegistry(MetricsMiddleware.global_stats)
        report   = registry.get_full_report(scenario=scenario, test_id=test_id, duration_sec=duration_sec)

        report["api_metrics"] = [{
            "endpoint":            target_url,
            "method":              "POST",
            "total_requests":      total_requests,
            "successful_requests": len(successes),
            "failed_requests":     len(failures),
            "success_rate":        success_rate,
            "rps":                 actual_rps,
            "latency": {
                "avg": avg_lat,
                "p50": _pct(50),
                "p90": _pct(90),
                "p95": _pct(95),
                "p99": _pct(99),
                "max": round(latencies[-1], 2) if latencies else 0.0,
            },
        }]

        persisted = await metrics_persistence.save_report(report, bearer)
        if persisted:
            logger.info("Load test metrics [%s] persisted to PI schema", test_id)

        return {
            "test_id":           test_id,
            "scenario":          scenario,
            "target_url":        target_url,
            "duration_sec":      duration_sec,
            "total_requests":    total_requests,
            "successful":        len(successes),
            "failed":            len(failures),
            "success_rate_pct":  success_rate,
            "rps":               actual_rps,
            "latency_ms": {
                "avg": avg_lat,
                "p50": _pct(50),
                "p90": _pct(90),
                "p95": _pct(95),
                "p99": _pct(99),
                "max": round(latencies[-1], 2) if latencies else 0.0,
            },
            "persisted_to_pi": persisted,
        }


load_generator = LoadGeneratorService()
