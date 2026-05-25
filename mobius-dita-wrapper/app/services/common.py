"""Shared orchestration helpers for the DITA service."""
from __future__ import annotations

import hashlib
import json
import uuid
from typing import Any

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.enums import EventType, JobState
from app.models import orm
from app.services import infra


def new_id(prefix: str) -> str:
    return f"{prefix}_{uuid.uuid4().hex[:24]}"


def checksum(text: str) -> str:
    return "sha256:" + hashlib.sha256(text.encode("utf-8")).hexdigest()


async def emit(
    session: AsyncSession,
    *,
    job_id: str,
    tenant_id: str,
    trace_context: dict,
    event_type: EventType,
    status: JobState | None = None,
    progress: float | None = None,
    message: str | None = None,
    artifact_refs: list[dict] | None = None,
) -> None:
    seq = await infra.next_sequence(job_id)
    event_id = new_id("evt")
    trace_id = (trace_context or {}).get("traceparent", "")
    session.add(orm.ExecutionEvent(
        event_id=event_id, event_sequence=seq, tenant_id=tenant_id, job_id=job_id,
        event_type=event_type.value, status=status.value if status else None,
        progress_percent=progress, message=message, artifact_refs=artifact_refs, trace_id=trace_id,
    ))
    await session.flush()
    await infra.publish_event(job_id, {
        "event_id": event_id, "event_sequence": seq, "tenant_id": tenant_id, "job_id": job_id,
        "event_type": event_type.value, "status": status.value if status else None,
        "progress_percent": progress, "message": message, "artifact_refs": artifact_refs,
        "occurred_at": infra.utcnow().isoformat(), "trace_id": trace_id,
    })


async def persist_artifact(
    session: AsyncSession, *, tenant_id: str, artifact_type: str, content: Any,
    job_id: str | None = None, document_id: str | None = None, chain_run_id: str | None = None,
    immutable: bool = False, classification: str | None = None,
) -> orm.ArtifactRef:
    text = content if isinstance(content, str) else json.dumps(content, default=str)
    ref = orm.ArtifactRef(
        artifact_ref_id=new_id("art"), artifact_type=artifact_type, storage_system="DITA",
        version="1", tenant_id=tenant_id, job_id=job_id, document_id=document_id,
        prompt_chain_run_id=chain_run_id, checksum=checksum(text),
        data_classification=classification, immutable=immutable,
        content={"value": content} if not isinstance(content, dict) else content,
    )
    session.add(ref)
    await session.flush()
    return ref


def artifact_dict(ref: orm.ArtifactRef) -> dict:
    return {
        "artifact_ref_id": ref.artifact_ref_id, "artifact_type": ref.artifact_type,
        "storage_system": ref.storage_system, "version": ref.version, "tenant_id": ref.tenant_id,
        "checksum": ref.checksum, "data_classification": ref.data_classification,
    }


async def add_lineage(session: AsyncSession, *, tenant_id: str, frm: str, to: str, edge_type: str, job_id: str | None = None) -> None:
    session.add(orm.LineageEdge(
        lineage_edge_id=new_id("ln"), tenant_id=tenant_id, from_artifact_ref=frm,
        to_artifact_ref=to, edge_type=edge_type, job_id=job_id,
    ))


async def find_idempotent(session: AsyncSession, tenant_id: str, endpoint: str, key: str) -> str | None:
    return (await session.execute(
        select(orm.IdempotencyKey.resource_id).where(
            orm.IdempotencyKey.tenant_id == tenant_id,
            orm.IdempotencyKey.endpoint == endpoint,
            orm.IdempotencyKey.idem_key == key,
        )
    )).scalar_one_or_none()


async def record_idempotent(session: AsyncSession, tenant_id: str, endpoint: str, key: str, resource_id: str) -> None:
    session.add(orm.IdempotencyKey(tenant_id=tenant_id, endpoint=endpoint, idem_key=key, resource_id=resource_id))
