"""Shared SSE event-stream helper used by document-jobs and chain-runs."""
from __future__ import annotations

import json

from app.core.enums import TERMINAL_STATES
from app.services import infra

_TERMINAL = {s.value for s in TERMINAL_STATES}


def _sse(ev: dict) -> str:
    return f"id: {ev.get('event_sequence','')}\nevent: {ev.get('event_type','message')}\ndata: {json.dumps(ev)}\n\n"


async def sse_event_stream(job_id: str, after: str = "$"):
    """Yield SSE frames for a job/run; replays history if after=='0', else tails live."""
    if after == "0":
        for _eid, ev in await infra.read_events(job_id, "0"):
            yield _sse(ev)
    async for ev in infra.tail_events(job_id, last_id="$" if after in ("$", "0") else after):
        if ev is None:
            yield ": heartbeat\n\n"
            continue
        yield _sse(ev)
        if ev.get("status") in _TERMINAL:
            break
