import statistics
import uuid
import time
from datetime import datetime
from typing import Dict, Any, List, Optional

from app.services.metrics_collector import system_collector
from app.middleware.metrics_middleware import _RPS_WINDOW_SEC


class MetricsRegistry:
    def __init__(self, middleware_stats: Dict[str, Any]) -> None:
        self.middleware_stats = middleware_stats

    # ------------------------------------------------------------------ #
    # Public API                                                           #
    # ------------------------------------------------------------------ #

    def get_api_metrics(self) -> List[Dict[str, Any]]:
        results = []
        now = time.time()

        for key, stats in self.middleware_stats.items():
            parts  = key.split(" ", 1)
            method = parts[0]
            path   = parts[1] if len(parts) > 1 else key

            latencies = list(stats["latencies"])
            total     = stats["total"]
            success   = stats["success"]
            failed    = stats["failed"]

            # RPS: requests in the current rolling window / window length
            recent = stats.get("timestamps")
            if recent:
                window_sec = min(_RPS_WINDOW_SEC, now - stats.get("first_seen", now) + 1)
                rps = len(recent) / window_sec
            else:
                elapsed = now - stats.get("first_seen", now)
                rps     = total / elapsed if elapsed > 0 else 0.0

            results.append({
                "endpoint":            path,
                "method":              method,
                "total_requests":      total,
                "successful_requests": success,
                "failed_requests":     failed,
                "rps":                 round(rps, 2),
                "success_rate":        round((success / total) * 100, 2) if total > 0 else 100.0,
                "latency": {
                    "p50": self._percentile(latencies, 50),
                    "p90": self._percentile(latencies, 90),
                    "p95": self._percentile(latencies, 95),
                    "p99": self._percentile(latencies, 99),
                    "avg": round(statistics.mean(latencies), 2) if latencies else 0.0,
                    "max": round(max(latencies), 2) if latencies else 0.0,
                },
            })

        return results

    def get_full_report(
        self,
        scenario:     str = "baseline",
        test_id:      Optional[str] = None,
        duration_sec: int = 0,
    ) -> Dict[str, Any]:
        api_metrics    = self.get_api_metrics()
        system_summary = system_collector.get_summary()

        return {
            "metadata": {
                "test_id":      test_id or str(uuid.uuid4())[:8],
                "scenario":     scenario,
                "timestamp":    datetime.utcnow().isoformat(),
                "duration_sec": duration_sec,
            },
            "api_metrics": api_metrics,
            "system_metrics": {
                "cpu_pct_avg": round(system_summary.get("cpu_avg",  0), 2),
                "mem_mb_avg":  round(system_summary.get("mem_avg",  0), 2),
                "gpu_pct_avg": round(system_summary.get("gpu_avg",  0), 2),
                "replicas":    system_summary.get("replicas", 1),
            },
        }

    # ------------------------------------------------------------------ #
    # Helpers                                                              #
    # ------------------------------------------------------------------ #

    @staticmethod
    def _percentile(data: List[float], p: int) -> float:
        """Linear-interpolation percentile (same algorithm as numpy/k6)."""
        if not data:
            return 0.0
        sorted_data = sorted(data)
        n   = len(sorted_data)
        idx = (n - 1) * p / 100.0
        lo  = int(idx)
        hi  = min(lo + 1, n - 1)
        return round(sorted_data[lo] + (sorted_data[hi] - sorted_data[lo]) * (idx - lo), 2)
