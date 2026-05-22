import logging
import time
from datetime import datetime
from typing import Any, Dict

from app.clients.pi_client import PIClient
from app.core.settings import settings

logger = logging.getLogger(__name__)


class MetricsPersistenceService:
    def __init__(self) -> None:
        self.pi_client = PIClient(settings.PI_SCHEMA_ID_DEPLOYMENT)

    async def save_report(self, report: Dict[str, Any], token: str) -> bool:
        """
        Persist one PI-entity row per endpoint found in `report["api_metrics"]`.
        System metrics (cpu/mem/gpu/replicas) are denormalised into every row.
        """
        try:
            metadata       = report.get("metadata", {})
            api_metrics    = report.get("api_metrics", [])
            system_metrics = report.get("system_metrics", {})

            test_id      = metadata.get("test_id",      f"test_{int(time.time())}")
            scenario     = metadata.get("scenario",     "baseline")
            duration_sec = metadata.get("duration_sec", 0)
            ts           = datetime.utcnow().strftime("%Y-%m-%d %H:%M:%S")

            saved = 0
            for ep in api_metrics:
                lat = ep.get("latency", {})
                instance = {
                    "test_id":        test_id,
                    "api_timestamp":  ts,
                    "scenario":       scenario,
                    "duration_sec":   duration_sec,
                    "endpoint":       ep.get("endpoint",   "unknown"),
                    "total_requests": ep.get("total_requests", 0),
                    "success_rate":   ep.get("success_rate",  100.0),
                    "rps":            ep.get("rps",           0.0),
                    "avg_latency":    lat.get("avg", 0.0),
                    "p50":            lat.get("p50", 0.0),
                    "p90":            lat.get("p90", 0.0),
                    "p95":            lat.get("p95", 0.0),
                    "p99":            lat.get("p99", 0.0),
                    "cpu_usage_pct":  system_metrics.get("cpu_pct_avg", 0.0),
                    "mem_usage_mb":   system_metrics.get("mem_mb_avg",  0.0),
                    "gpu_usage_pct":  system_metrics.get("gpu_pct_avg", 0.0),
                    "replica_count":       system_metrics.get("replicas",    1),
                }

                logger.info(
                    "Persisting load-test metrics | test=%s endpoint=%s",
                    test_id, instance["endpoint"],
                )
                await self.pi_client.save_load_test_metrics(instance, token)
                saved += 1

            logger.info("Persisted %d endpoint rows for test_id=%s", saved, test_id)
            return True

        except Exception as exc:
            logger.error("Failed to persist load-test metrics: %s", exc)
            return False


metrics_persistence = MetricsPersistenceService()
