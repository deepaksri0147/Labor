"""Document lifecycle (workflow state machine) and feedback endpoints."""
from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException, status
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.auth import Principal, current_principal, enforce_tenant
from app.core.enums import WORKFLOW_TRANSITIONS, WorkflowState
from app.db.session import get_session
from app.models import orm
from app.schemas.dita import (
    ApproveRequest,
    FeedbackEvent,
    PublishRequest,
    RejectRequest,
    RequestRepairRequest,
    RollbackRequest,
    SubmitReviewRequest,
)
from app.services import common

router = APIRouter(tags=["Document Workflow"])
SEC = [{"bearerAuth": []}]


async def _load_doc(session, document_id, principal) -> orm.DocumentWorkflow:
    doc = await session.get(orm.DocumentWorkflow, document_id)
    if not doc:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "document not found")
    enforce_tenant(principal, doc.tenant_id)
    return doc


def _guard(doc: orm.DocumentWorkflow, target: WorkflowState) -> None:
    current = WorkflowState(doc.workflow_state)
    allowed = WORKFLOW_TRANSITIONS.get(current, set())
    if target not in allowed:
        raise HTTPException(status.HTTP_409_CONFLICT,
                            f"illegal transition {current.value} -> {target.value}")


async def _transition(session, doc: orm.DocumentWorkflow, target: WorkflowState, event, message=None):
    _guard(doc, target)
    doc.workflow_state = target.value
    doc.version += 1  # optimistic-lock token bump
    await common.emit(session, job_id=doc.document_id, tenant_id=doc.tenant_id, trace_context={},
                      event_type=event, message=message)


@router.post("/documents/{document_id}/submit-review")
async def submit_review(document_id: str, body: SubmitReviewRequest,
                        principal: Principal = Depends(current_principal),
                        session: AsyncSession = Depends(get_session)) -> dict:
    from app.core.enums import EventType
    doc = await _load_doc(session, document_id, principal)
    await _transition(session, doc, WorkflowState.ready_for_review, EventType.document_review_requested, body.comment)
    return {"document_id": document_id, "workflow_state": doc.workflow_state}


@router.post("/documents/{document_id}/approve")
async def approve(document_id: str, body: ApproveRequest, principal: Principal = Depends(current_principal),
                  session: AsyncSession = Depends(get_session)) -> dict:
    from app.core.enums import EventType
    doc = await _load_doc(session, document_id, principal)
    await _transition(session, doc, WorkflowState.approved, EventType.document_approved, body.approval_comment)
    if body.publish_after_approval:
        await _transition(session, doc, WorkflowState.published, EventType.document_published)
    return {"document_id": document_id, "workflow_state": doc.workflow_state}


@router.post("/documents/{document_id}/reject")
async def reject(document_id: str, body: RejectRequest, principal: Principal = Depends(current_principal),
                 session: AsyncSession = Depends(get_session)) -> dict:
    from app.core.enums import EventType
    doc = await _load_doc(session, document_id, principal)
    await _transition(session, doc, WorkflowState.rejected, EventType.document_rejected, body.rejection_reason)
    if body.create_repair_request:
        await _transition(session, doc, WorkflowState.repair_required, EventType.job_progress)
    return {"document_id": document_id, "workflow_state": doc.workflow_state}


@router.post("/documents/{document_id}/request-repair")
async def request_repair(document_id: str, body: RequestRepairRequest,
                         principal: Principal = Depends(current_principal),
                         session: AsyncSession = Depends(get_session)) -> dict:
    from app.core.enums import EventType
    doc = await _load_doc(session, document_id, principal)
    await _transition(session, doc, WorkflowState.repair_required, EventType.job_progress, body.repair_reason)
    return {"document_id": document_id, "workflow_state": doc.workflow_state}


@router.post("/documents/{document_id}/publish")
async def publish(document_id: str, body: PublishRequest, principal: Principal = Depends(current_principal),
                  session: AsyncSession = Depends(get_session)) -> dict:
    from app.core.enums import EventType
    doc = await _load_doc(session, document_id, principal)
    _guard(doc, WorkflowState.published)
    # produce an immutable, versioned published artifact
    ref = await common.persist_artifact(session, tenant_id=doc.tenant_id, artifact_type="published_document",
                                        content={"document_id": document_id, "channel": body.publication_channel,
                                                 "version": doc.current_version},
                                        document_id=document_id, immutable=body.immutable)
    session.add(orm.DocumentVersion(document_id=document_id, version=doc.current_version,
                                    published_artifact_ref=ref.artifact_ref_id, immutable=body.immutable,
                                    snapshot={"workflow_state": "published", "channel": body.publication_channel}))
    doc.workflow_state = WorkflowState.published.value
    doc.refs = {**(doc.refs or {}), "published_artifact_ref": ref.artifact_ref_id}
    doc.version += 1
    await common.emit(session, job_id=document_id, tenant_id=doc.tenant_id, trace_context={},
                      event_type=EventType.document_published, artifact_refs=[common.artifact_dict(ref)])
    return {"document_id": document_id, "workflow_state": doc.workflow_state,
            "published_artifact_ref": ref.artifact_ref_id}


@router.post("/documents/{document_id}/rollback")
async def rollback(document_id: str, body: RollbackRequest, principal: Principal = Depends(current_principal),
                   session: AsyncSession = Depends(get_session)) -> dict:
    from app.core.enums import EventType
    doc = await _load_doc(session, document_id, principal)
    target = (await session.execute(
        select(orm.DocumentVersion).where(orm.DocumentVersion.document_id == document_id,
                                          orm.DocumentVersion.version == body.target_version)
    )).scalar_one_or_none()
    if not target:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "target version not found")
    await _transition(session, doc, WorkflowState.rolled_back, EventType.job_progress, body.rollback_reason)
    doc.current_version = body.target_version
    return {"document_id": document_id, "workflow_state": doc.workflow_state, "current_version": body.target_version}


@router.get("/documents/{document_id}/versions")
async def versions(document_id: str, principal: Principal = Depends(current_principal),
                   session: AsyncSession = Depends(get_session)) -> dict:
    await _load_doc(session, document_id, principal)
    rows = (await session.execute(
        select(orm.DocumentVersion.version, orm.DocumentVersion.published_artifact_ref,
               orm.DocumentVersion.immutable, orm.DocumentVersion.created_at)
        .where(orm.DocumentVersion.document_id == document_id)
    )).all()
    return {"document_id": document_id,
            "versions": [{"version": v, "published_artifact_ref": r, "immutable": im, "created_at": c.isoformat()}
                         for v, r, im, c in rows]}


@router.get("/documents/{document_id}/diff")
async def diff(document_id: str, from_version: str, to_version: str,
               principal: Principal = Depends(current_principal),
               session: AsyncSession = Depends(get_session)) -> dict:
    await _load_doc(session, document_id, principal)
    async def snap(v):
        row = (await session.execute(
            select(orm.DocumentVersion.snapshot).where(orm.DocumentVersion.document_id == document_id,
                                                       orm.DocumentVersion.version == v)
        )).scalar_one_or_none()
        return row or {}
    a, b = await snap(from_version), await snap(to_version)
    keys = set(a) | set(b)
    changes = {k: {"from": a.get(k), "to": b.get(k)} for k in keys if a.get(k) != b.get(k)}
    return {"document_id": document_id, "from_version": from_version, "to_version": to_version, "changes": changes}


@router.post("/documents/{document_id}/feedback")
async def add_feedback(document_id: str, body: FeedbackEvent, principal: Principal = Depends(current_principal),
                       session: AsyncSession = Depends(get_session)) -> dict:
    from app.core.enums import EventType
    doc = await _load_doc(session, document_id, principal)
    fid = common.new_id("fb")
    session.add(orm.FeedbackRow(feedback_id=fid, tenant_id=doc.tenant_id, document_id=document_id,
                                feedback_type=body.feedback_type if isinstance(body.feedback_type, str) else body.feedback_type.value,
                                severity=body.severity if isinstance(body.severity, str) else body.severity.value,
                                comment=body.comment, spec=body.model_dump(mode="json")))
    await common.emit(session, job_id=document_id, tenant_id=doc.tenant_id, trace_context={},
                      event_type=EventType.feedback_added, message=body.comment)
    return {"feedback_id": fid, "document_id": document_id}


@router.post("/feedback/{feedback_id}/convert-to-repair")
async def convert_feedback_to_repair(feedback_id: str, principal: Principal = Depends(current_principal),
                                     session: AsyncSession = Depends(get_session)) -> dict:
    fb = await session.get(orm.FeedbackRow, feedback_id)
    if not fb:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "feedback not found")
    enforce_tenant(principal, fb.tenant_id)
    doc = await session.get(orm.DocumentWorkflow, fb.document_id)
    if doc and WorkflowState(doc.workflow_state) in WORKFLOW_TRANSITIONS and \
            WorkflowState.repair_required in WORKFLOW_TRANSITIONS.get(WorkflowState(doc.workflow_state), set()):
        doc.workflow_state = WorkflowState.repair_required.value
        doc.version += 1
    fb.resolved = True
    return {"feedback_id": feedback_id, "document_id": fb.document_id,
            "workflow_state": doc.workflow_state if doc else None, "converted": True}
