import os
import asyncio
import logging
from collections import deque
from datetime import datetime
from typing import Any, Dict, List, Optional

import psutil

logger = logging.getLogger(__name__)

# ── GPU support via pynvml (optional) ──────────────────────────────────────
try:
    import pynvml
    pynvml.nvmlInit()
    _GPU_COUNT     = pynvml.nvmlDeviceGetCount()
    _GPU_AVAILABLE = _GPU_COUNT > 0
    logger.info(f"pynvml initialised — {_GPU_COUNT} GPU(s) detected")
except Exception:
    _GPU_AVAILABLE = False
    _GPU_COUNT     = 0


def _gpu_percent() -> float:
    if not _GPU_AVAILABLE:
        return 0.0
    try:
        total = 0.0
        for i in range(_GPU_COUNT):
            handle = pynvml.nvmlDeviceGetHandleByIndex(i)
            total += pynvml.nvmlDeviceGetUtilizationRates(handle).gpu
        return round(total / _GPU_COUNT, 2)
    except Exception:
        return 0.0


# ── Kubernetes replica count (optional) ────────────────────────────────────
def _k8s_replica_count() -> int:
    try:
        from kubernetes import client as k8s_client, config as k8s_config

        try:
            k8s_config.load_incluster_config()
        except Exception:
            k8s_config.load_kube_config()

        namespace = os.environ.get("K8S_NAMESPACE", "default")
        app_label = os.environ.get("K8S_APP_LABEL", "llm-gateway")

        api   = k8s_client.AppsV1Api()
        items = api.list_namespaced_deployment(
            namespace, label_selector=f"app={app_label}"
        ).items

        count = sum(d.status.ready_replicas or 0 for d in items)
        return count if count > 0 else 1
    except Exception:
        return 1


# ── Collector ───────────────────────────────────────────────────────────────

class SystemMetricsCollector:
    """
    Polls CPU / memory / GPU every `interval_sec` seconds in the background.
    Keeps at most one hour of samples (deque, O(1) append/trim).
    """

    def __init__(self, interval_sec: float = 1.0) -> None:
        self.interval_sec = interval_sec
        self._history: deque = deque(maxlen=3600)
        self._running = False
        self._task: Optional[asyncio.Task] = None

    async def start(self) -> None:
        if self._running:
            return
        self._running = True
        self._task    = asyncio.create_task(self._loop())
        logger.info("SystemMetricsCollector started")

    async def stop(self) -> None:
        self._running = False
        if self._task:
            self._task.cancel()
            try:
                await self._task
            except asyncio.CancelledError:
                pass
        logger.info("SystemMetricsCollector stopped")

    async def _loop(self) -> None:
        while self._running:
            try:
                self._history.append(self._snapshot())
            except Exception as exc:
                logger.error(f"SystemMetricsCollector error: {exc}")
            await asyncio.sleep(self.interval_sec)

    def _snapshot(self) -> Dict[str, Any]:
        return {
            "timestamp": datetime.utcnow().isoformat(),
            "cpu_pct":   psutil.cpu_percent(),
            "mem_mb":    psutil.virtual_memory().used / (1024 * 1024),
            "gpu_pct":   _gpu_percent(),
            "replicas":  _k8s_replica_count(),
        }

    def get_current(self) -> Dict[str, Any]:
        """Return the latest single sample."""
        if self._history:
            return dict(self._history[-1])
        return self._snapshot()

    def get_summary(self, since: Optional[datetime] = None) -> Dict[str, Any]:
        """
        Return averaged metrics over `since` → now.
        Falls back to all history if `since` is None.
        """
        data: List[Dict[str, Any]] = list(self._history)

        if since:
            data = [m for m in data if datetime.fromisoformat(m["timestamp"]) >= since]

        if not data:
            return {
                "cpu_avg":      0.0,
                "mem_avg":      0.0,
                "gpu_avg":      0.0,
                "replicas":     1,
                "sample_count": 0,
            }

        n = len(data)
        return {
            "cpu_avg":      round(sum(m["cpu_pct"]  for m in data) / n, 2),
            "mem_avg":      round(sum(m["mem_mb"]   for m in data) / n, 2),
            "gpu_avg":      round(sum(m["gpu_pct"]  for m in data) / n, 2),
            "replicas":     data[-1]["replicas"],
            "sample_count": n,
        }


# Singleton used across the application
system_collector = SystemMetricsCollector()
