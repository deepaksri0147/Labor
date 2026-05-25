"""Redis-backed infrastructure: cache-prefix store, job queue, event streams.

- Cache prefixes: declared, TTL-bounded reusable context (Redis string + TTL + hit counter).
- Job queue: a Redis list used as a FIFO work queue consumed by the worker.
- Event streams: one Redis Stream per job_id, holding JobEvents for SSE + replay,
  trimmed to the retention window.
"""
from __future__ import annotations

import json
from datetime import datetime, timezone
from typing import Any

import redis.asyncio as aioredis

from app.core.config import get_settings

_settings = get_settings()
_redis: aioredis.Redis | None = None


def get_redis() -> aioredis.Redis:
    global _redis
    if _redis is None:
        _redis = aioredis.from_url(_settings.redis_url, decode_responses=True)
    return _redis


# ---- cache prefix ---------------------------------------------------------
def _prefix_key(cache_prefix_id: str) -> str:
    return f"cacheprefix:{cache_prefix_id}"


async def store_cache_prefix(cache_prefix_id: str, content: str, ttl_seconds: int, meta: dict) -> None:
    r = get_redis()
    payload = {"content": content, "meta": json.dumps(meta), "hit_count": "0"}
    key = _prefix_key(cache_prefix_id)
    await r.hset(key, mapping=payload)
    await r.expire(key, ttl_seconds)


async def get_cache_prefix(cache_prefix_id: str) -> dict[str, Any] | None:
    r = get_redis()
    key = _prefix_key(cache_prefix_id)
    data = await r.hgetall(key)
    if not data:
        return None
    ttl = await r.ttl(key)
    return {
        "content": data.get("content", ""),
        "meta": json.loads(data.get("meta", "{}")),
        "hit_count": int(data.get("hit_count", 0)),
        "ttl_seconds": ttl if ttl and ttl > 0 else 0,
    }


async def resolve_prefixes(cache_prefix_ids: list[str]) -> list[str]:
    """Return the prefix contents in order, incrementing hit counters."""
    r = get_redis()
    contents: list[str] = []
    for cpid in cache_prefix_ids:
        key = _prefix_key(cpid)
        data = await r.hget(key, "content")
        if data is not None:
            contents.append(data)
            await r.hincrby(key, "hit_count", 1)
    return contents


# ---- job queue ------------------------------------------------------------
async def enqueue_job(job_id: str) -> None:
    await get_redis().rpush(_settings.queue_name, job_id)


async def dequeue_job(timeout: int = 5) -> str | None:
    res = await get_redis().blpop([_settings.queue_name], timeout=timeout)
    if res is None:
        return None
    _, job_id = res
    return job_id


# ---- event stream ---------------------------------------------------------
def _stream_key(job_id: str) -> str:
    return f"{_settings.event_stream_prefix}{job_id}"


async def next_sequence(job_id: str) -> int:
    return int(await get_redis().incr(f"seq:{job_id}"))


async def publish_event(job_id: str, event: dict[str, Any]) -> str:
    """Append a JobEvent to the job's Redis Stream; returns the stream entry id."""
    r = get_redis()
    key = _stream_key(job_id)
    flat = {"data": json.dumps(event, default=_json_default)}
    entry_id = await r.xadd(key, flat)
    await r.expire(key, _settings.event_retention_seconds)
    return entry_id


async def read_events(job_id: str, last_id: str = "0") -> list[tuple[str, dict[str, Any]]]:
    r = get_redis()
    entries = await r.xrange(_stream_key(job_id), min=f"({last_id}" if last_id != "0" else "-", max="+")
    out = []
    for entry_id, fields in entries:
        out.append((entry_id, json.loads(fields["data"])))
    return out


async def tail_events(job_id: str, last_id: str = "$", block_ms: int = 15000):
    """Async generator yielding new events as they arrive (for SSE)."""
    r = get_redis()
    key = _stream_key(job_id)
    cursor = last_id
    while True:
        resp = await r.xread({key: cursor}, count=10, block=block_ms)
        if not resp:
            yield None  # heartbeat opportunity
            continue
        for _stream, entries in resp:
            for entry_id, fields in entries:
                cursor = entry_id
                yield json.loads(fields["data"])


def _json_default(o: Any):
    if isinstance(o, datetime):
        return o.isoformat()
    return str(o)


def utcnow() -> datetime:
    return datetime.now(timezone.utc)
