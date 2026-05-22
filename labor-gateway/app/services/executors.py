"""Execution backend abstraction.

The pluggable-executor contract: a labor call names an executor by id. The input and
output shapes are identical regardless of which executor serves it. Today the registered
executors are inference models reached via the EXISTING /inference/chat endpoint; new
executor kinds implement the same Executor protocol and register here.

This is the single point where the new labor tier consumes existing gateway code.
"""
from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Protocol

import httpx

from app.core.config import get_settings

_settings = get_settings()


@dataclass
class ExecutionResult:
    raw_text: str
    prompt_tokens: int
    completion_tokens: int
    cache_read_tokens: int
    cache_write_tokens: int
    executor_id: str
    provider: str | None = None


class Executor(Protocol):
    executor_id: str
    supports_batch: bool
    supports_cache_prefix: bool

    async def execute(
        self, *, messages: list[dict[str, Any]], parameters: dict[str, Any], cached_prefixes: list[str]
    ) -> ExecutionResult: ...


class InferenceExecutor:
    """Wraps the existing /inference/chat capability. One executor per backing model."""

    def __init__(self, executor_id: str, model: str, provider: str = "internal"):
        self.executor_id = executor_id
        self.model = model
        self.provider = provider
        self.supports_batch = True
        self.supports_cache_prefix = True

    async def execute(
        self, *, messages, parameters, cached_prefixes
    ) -> ExecutionResult:
        import asyncio

        # Prepend cached prefixes as leading system content.
        full_messages = [{"role": "system", "content": p} for p in cached_prefixes] + messages
        payload = {
            "tool": self.provider,
            "endpoint": "chat_completions",
            "input": {"model": self.model, "messages": full_messages, **parameters},
        }
        url = _settings.inference_base_url.rstrip("/") + _settings.inference_chat_path
        headers = {"Content-Type": "application/json", "Accept": "application/json"}
        if _settings.inference_token:
            headers["Authorization"] = f"Bearer {_settings.inference_token}"

        async with httpx.AsyncClient(timeout=_settings.inference_timeout_seconds) as client:
            resp = await client.post(url, json=payload, headers=headers)
            resp.raise_for_status()
            data = resp.json()
            
            # The inference API returns a task_id for polling
            task_id = data.get("data", {}).get("task_id")
            if not task_id:
                raise Exception(f"Failed to get task_id from inference API: {data}")
            
            poll_url = _settings.inference_base_url.rstrip("/") + f"/job/{task_id}"
            
            # Polling loop
            while True:
                poll_resp = await client.get(poll_url, headers=headers)
                poll_resp.raise_for_status()
                poll_data = poll_resp.json()
                
                status = poll_data.get("data", {}).get("status")
                if status == "SUCCESS":
                    result_data = poll_data["data"]["result"]["result"]
                    break
                elif status == "FAILURE":
                    error_msg = poll_data.get("data", {}).get("error", "Unknown error")
                    raise Exception(f"Inference job failed: {error_msg}")
                    
                await asyncio.sleep(2)

        usage = result_data.get("usage", {}) or {}
        return ExecutionResult(
            raw_text=_extract_text(result_data),
            prompt_tokens=int(usage.get("prompt_tokens", 0)),
            completion_tokens=int(usage.get("completion_tokens", 0)),
            cache_read_tokens=int(usage.get("cache_read_tokens", usage.get("cache_read_input_tokens", 0))),
            cache_write_tokens=int(usage.get("cache_write_tokens", usage.get("cache_creation_input_tokens", 0))),
            executor_id=self.executor_id,
            provider=self.provider,
        )


def _extract_text(data: dict[str, Any]) -> str:
    # tolerate both OpenAI-style and content-block style responses
    if "choices" in data and data["choices"]:
        msg = data["choices"][0].get("message", {})
        return msg.get("content", "") or ""
    if "content" in data:
        c = data["content"]
        if isinstance(c, list):
            return "".join(b.get("text", "") for b in c if isinstance(b, dict))
        return str(c)
    return data.get("output", data.get("text", ""))


class ExecutorRegistry:
    def __init__(self) -> None:
        self._executors: dict[str, Executor] = {}

    def register(self, executor: Executor) -> None:
        self._executors[executor.executor_id] = executor

    def get(self, executor_id: str) -> Executor:
        if executor_id not in self._executors:
            raise KeyError(f"unknown executor_id: {executor_id}")
        return self._executors[executor_id]

    def choose_auto(self, *, data_classification: str | None) -> Executor:
        # Minimal auto policy: first available. A real policy consults ModelPolicy.
        if not self._executors:
            raise KeyError("no executors registered")
        return next(iter(self._executors.values()))

    def descriptors(self) -> list[dict[str, Any]]:
        return [
            {
                "executor_id": e.executor_id,
                "supports_batch": getattr(e, "supports_batch", False),
                "supports_cache_prefix": getattr(e, "supports_cache_prefix", False),
                "status": "available",
            }
            for e in self._executors.values()
        ]


# module-level registry, seeded at startup
registry = ExecutorRegistry()


def seed_default_executors() -> None:
    if not registry._executors:
        registry.register(InferenceExecutor("default", model="gpt-4o", provider="openai"))
