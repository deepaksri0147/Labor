"""Wrapper API client — the SINGLE place that talks to the external CRUD service.

Exposes exactly three calls, matching the wrapper's contract:

    POST /ingest?datamodelName=<table>       body: [ {...row}, ... ]
    POST /retrieve?datamodelName=<table>     body: { <filter> }
    PUT  /update?schemaName=<table>          body: [ {...row with pk}, ... ]

Callers build the payload (a list of row dicts or a filter dict) and invoke `ingest`,
`retrieve`, or `update`. Every request and response is logged with the datamodel name,
row count, HTTP status, and latency for traceability.

Bearer-token propagation
------------------------
The wrapper is called with the bearer token forwarded from the inbound HTTP request
when one is present. The token is stored in a `ContextVar` that is set per-request
(by the auth dependency in app.core.auth) and per-worker-job (by app.worker.main
after it dequeues). For local/dev runs where the primary API has auth disabled, a
configured wrapper fallback token is used so wrapper calls still send Authorization.

Also exposes small helpers — `orm_to_dict`, `as_rows`, `retrieve_one` — that callers use
to bridge SQLAlchemy ORM instances and wrapper responses without writing serialization
boilerplate at every call site.
"""
from __future__ import annotations

import contextvars
import json
import logging
import time
from datetime import datetime
from enum import Enum
from typing import Any

import httpx
from fastapi import HTTPException, status

from app.core.config import get_settings

_settings = get_settings()
log = logging.getLogger("labor.wrapper_api")


# ---- per-request/per-job bearer token -------------------------------------
_current_token: contextvars.ContextVar[str | None] = contextvars.ContextVar(
    "wrapper_api_token", default=None
)


def set_token(token: str | None) -> None:
    """Bind the bearer token used by subsequent wrapper calls in this context."""
    _current_token.set(token)


def get_token() -> str | None:
    return _current_token.get()


# ---- table-name mapping ---------------------------------------------------
# datamodelName / schemaName values sent to the wrapper. Sourced from each ORM
# class's __tablename__.
LABOR_JOB = "labor_job"
LABOR_BATCH = "labor_batch"
BATCH_ITEM = "batch_item"
ARTIFACT_REF = "artifact_ref"
EXECUTION_EVENT = "execution_event"
COST_RECORD = "cost_record"
IDEMPOTENCY_KEY = "idempotency_key"
EXECUTOR_POLICY = "executor_policy"
MODEL_POLICY = "model_policy"


# ---- JSON-safe serialization ----------------------------------------------
def _to_jsonable(value: Any) -> Any:
    if isinstance(value, datetime):
        return value.isoformat()
    if isinstance(value, Enum):
        return value.value
    if isinstance(value, dict):
        return {k: _to_jsonable(v) for k, v in value.items()}
    if isinstance(value, list):
        return [_to_jsonable(v) for v in value]
    return value


def orm_to_dict(obj: Any) -> dict:
    """Convert a SQLAlchemy ORM instance to a JSON-safe dict of its columns."""
    out: dict = {}
    for col in obj.__table__.columns:
        out[col.name] = _to_jsonable(getattr(obj, col.name))
    return out


def apply_to_orm(obj: Any, row: dict) -> Any:
    """Copy fields from `row` onto `obj` (skips unknown keys)."""
    cols = {c.name for c in obj.__table__.columns}
    for k, v in row.items():
        if k in cols:
            setattr(obj, k, v)
    return obj


# ---- response normalization ----------------------------------------------
def as_rows(resp: Any) -> list[dict]:
    """Normalize a wrapper response to a list of row dicts."""
    if resp is None:
        return []
    if isinstance(resp, list):
        return [r for r in resp if isinstance(r, dict)]
    if isinstance(resp, dict):
        for key in ("data", "items", "results", "rows", "records"):
            inner = resp.get(key)
            if isinstance(inner, list):
                return [r for r in inner if isinstance(r, dict)]
            if isinstance(inner, dict):
                return [inner]
        # single object response
        return [resp]
    return []


def first_row(resp: Any) -> dict | None:
    rows = as_rows(resp)
    return rows[0] if rows else None


# ---- HTTP plumbing --------------------------------------------------------
def _clean_token(token: str | None) -> str | None:
    if not token:
        return None
    token = token.strip().strip("\"'")
    if not token or (token.startswith("${") and token.endswith("}")):
        return None
    if token.lower().startswith("bearer "):
        token = token[7:].strip()
    return token or None


def _auth_token() -> str | None:
    # Prefer user/inbound identity. Fall back to a wrapper-specific service token
    # for local auth-disabled runs. The inference token fallback preserves existing
    # local env files that only had one platform bearer token configured.
    return (
        _clean_token(_current_token.get())
        or _clean_token(_settings.wrapper_token)
        or _clean_token(_settings.inference_token)
    )


def _headers() -> dict[str, str]:
    h = {"accept": "application/json", "Content-Type": "application/json"}
    token = _auth_token()
    if token:
        h["Authorization"] = f"Bearer {token}"
    return h


def _base_url() -> str:
    return _settings.wrapper_base_url.rstrip("/")


def _operation(method: str, path: str) -> str:
    """Human label for the operation (INGEST / RETRIEVE / UPDATE)."""
    if path.endswith("/ingest"):
        return "INGEST"
    if path.endswith("/retrieve"):
        return "RETRIEVE"
    if path.endswith("/update"):
        return "UPDATE"
    return f"{method} {path}"


def _dump(obj: Any, limit: int = 4000) -> str:
    """Serialize a payload/response to compact JSON for logging, with size cap."""
    try:
        s = json.dumps(obj, default=str, ensure_ascii=False)
    except Exception:  # pragma: no cover — defensive
        s = repr(obj)
    return s if len(s) <= limit else s[:limit] + f"...<truncated {len(s) - limit} chars>"


async def _request(method: str, path: str, *, params: dict, body: Any) -> Any:
    url = f"{_base_url()}{path}"
    rows = len(body) if isinstance(body, list) else (1 if body else 0)
    schema_name = params.get("datamodelName") or params.get("schemaName") or "?"
    param_key = "datamodelName" if "datamodelName" in params else (
        "schemaName" if "schemaName" in params else "?"
    )
    op = _operation(method, path)
    full_url = f"{url}?{param_key}={schema_name}"

    token = _auth_token()
    auth_tag = "Bearer <present>" if token else "<absent>"
    log.info("=" * 80)
    log.info("[%s] -> %s %s", op, method, full_url)
    log.info("[%s]    %s=%s  rows=%d  auth=%s", op, param_key, schema_name, rows, auth_tag)
    log.info("[%s]    payload=%s", op, _dump(body))

    start = time.perf_counter()
    try:
        async with httpx.AsyncClient(timeout=_settings.wrapper_timeout_seconds) as c:
            r = await c.request(method, url, params=params, headers=_headers(), json=body)
    except httpx.ConnectError as e:
        elapsed_ms = (time.perf_counter() - start) * 1000.0
        log.error("[%s] <- CONNECT ERROR  %s=%s  elapsed_ms=%.1f  err=%s",
                  op, param_key, schema_name, elapsed_ms, e)
        log.info("=" * 80)
        raise HTTPException(
            status_code=status.HTTP_502_BAD_GATEWAY,
            detail={
                "error": "wrapper_unreachable",
                "operation": op,
                "target": schema_name,
                "url": full_url,
                "message": f"Cannot connect to wrapper service at {_base_url()}: {e}",
            },
        )
    except httpx.TimeoutException as e:
        elapsed_ms = (time.perf_counter() - start) * 1000.0
        log.error("[%s] <- TIMEOUT  %s=%s  elapsed_ms=%.1f  err=%s",
                  op, param_key, schema_name, elapsed_ms, e)
        log.info("=" * 80)
        raise HTTPException(
            status_code=status.HTTP_504_GATEWAY_TIMEOUT,
            detail={
                "error": "wrapper_timeout",
                "operation": op,
                "target": schema_name,
                "url": full_url,
                "timeout_seconds": _settings.wrapper_timeout_seconds,
                "message": str(e),
            },
        )
    except httpx.HTTPError as e:
        elapsed_ms = (time.perf_counter() - start) * 1000.0
        log.error("[%s] <- TRANSPORT ERROR  %s=%s  elapsed_ms=%.1f  err=%s",
                  op, param_key, schema_name, elapsed_ms, e)
        log.info("=" * 80)
        raise HTTPException(
            status_code=status.HTTP_502_BAD_GATEWAY,
            detail={
                "error": "wrapper_transport_error",
                "operation": op,
                "target": schema_name,
                "url": full_url,
                "message": str(e),
            },
        )

    elapsed_ms = (time.perf_counter() - start) * 1000.0
    response_text = r.text or ""
    response_json: Any = None
    if r.content:
        try:
            response_json = r.json()
        except ValueError:
            response_json = None

    if r.status_code >= 400:
        body_for_log = response_json if response_json is not None else response_text
        log.error("[%s] <- FAILED  status=%d  elapsed_ms=%.1f  %s=%s",
                  op, r.status_code, elapsed_ms, param_key, schema_name)
        log.error("[%s]    response_body=%s", op, _dump(body_for_log))
        log.info("=" * 80)
        # Forward the wrapper's status code (clamped to a valid range) and body so the
        # caller can see exactly what the wrapper rejected.
        forwarded_status = r.status_code if 400 <= r.status_code < 600 else status.HTTP_502_BAD_GATEWAY
        raise HTTPException(
            status_code=forwarded_status,
            detail={
                "error": "wrapper_error_response",
                "operation": op,
                "target": schema_name,
                "url": full_url,
                "wrapper_status": r.status_code,
                "wrapper_body": response_json if response_json is not None else response_text[:1000],
            },
        )

    resp_rows = len(as_rows(response_json))
    log.info("[%s] <- OK  status=%d  elapsed_ms=%.1f  resp_rows=%d",
             op, r.status_code, elapsed_ms, resp_rows)
    log.info("[%s]    response=%s", op, _dump(response_json if response_json is not None else response_text))
    log.info("=" * 80)
    return response_json


# ---- public API: ingest / retrieve / update -------------------------------
async def ingest(datamodel: str, rows: list[dict]) -> Any:
    """POST /ingest?datamodelName={datamodel} with a JSON array of rows."""
    payload = [_to_jsonable(r) for r in rows]
    return await _request("POST", "/ingest", params={"datamodelName": datamodel}, body=payload)


async def retrieve(datamodel: str, filter: dict | None = None) -> Any:
    """POST /retrieve?datamodelName={datamodel} with a JSON filter body."""
    body = _to_jsonable(filter or {})
    return await _request("POST", "/retrieve", params={"datamodelName": datamodel}, body=body)


async def update(schema: str, rows: list[dict]) -> Any:
    """PUT /update?schemaName={schema} with a JSON array of rows (each must carry pk)."""
    payload = [_to_jsonable(r) for r in rows]
    return await _request("PUT", "/update", params={"schemaName": schema}, body=payload)


# ---- convenience: retrieve a single row -----------------------------------
async def retrieve_one(datamodel: str, filter: dict) -> dict | None:
    return first_row(await retrieve(datamodel, filter))
