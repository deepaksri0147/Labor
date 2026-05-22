import logging
from typing import Any, Dict, List

from fastapi import APIRouter, HTTPException, Security, Body
from fastapi.security import HTTPBearer, HTTPAuthorizationCredentials

from app.schemas.batch import (
    ApiErrorResponse,
    BatchEstimateItem,
    BatchEstimateRequest,
    BatchEstimateResponse,
    BatchFetchWithUsageResponse,
    BatchSubmitRequest,
    BatchStatusResponse,
    PromptTemplateCreateRequest,
    PromptTemplateCreateResponse,
    PromptTemplateRenderRequest,
    PromptTemplateRenderResponse,
)
from app.services.batch.anthropic_batch_service import AnthropicBatchService
from app.services.batch.prompt_template_service import PromptTemplateService
from app.services.vocabulary_service import VocabularyService
from app.celery_app import celery_app
from app.utils.utils import decode_jwt, extract_identity

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/batch", tags=["Batch Processing"])
auth_scheme = HTTPBearer()

_MODEL_CONTEXT_WINDOWS = {
    "claude-sonnet-4-6": 200_000,
    "claude-3-7-sonnet-latest": 200_000,
    "claude-3-5-sonnet-latest": 200_000,
    "claude-3-5-haiku-latest": 200_000,
}

_BATCH_ERROR_RESPONSES = {
    400: {"model": ApiErrorResponse, "description": "Invalid request payload"},
    401: {"model": ApiErrorResponse, "description": "Missing/invalid bearer token"},
    404: {"model": ApiErrorResponse, "description": "Batch or template not found"},
    409: {"model": ApiErrorResponse, "description": "State conflict (still processing / not ended)"},
    429: {"model": ApiErrorResponse, "description": "Rate-limited by upstream"},
    502: {"model": ApiErrorResponse, "description": "Upstream gateway failure"},
    503: {"model": ApiErrorResponse, "description": "Circuit breaker open / upstream unavailable"},
}


def _get_identity(credentials: HTTPAuthorizationCredentials):
    if not credentials or not credentials.credentials:
        raise HTTPException(
            status_code=401,
            detail={"error": "UNAUTHORIZED", "message": "Authorization token is required"},
        )
    decoded = decode_jwt(credentials.credentials)
    if not decoded:
        raise HTTPException(
            status_code=401,
            detail={"error": "UNAUTHORIZED", "message": "Invalid or corrupted token"},
        )
    return credentials.credentials, extract_identity(decoded)


def _estimate_tokens_from_text(text: str) -> int:
    # Fast approximation suitable for pre-flight checks.
    return max(1, len(text) // 4)


def _extract_text_tokens(value: Any) -> int:
    if value is None:
        return 0
    if isinstance(value, str):
        return _estimate_tokens_from_text(value)
    if isinstance(value, list):
        total = 0
        for part in value:
            if isinstance(part, dict):
                total += _estimate_tokens_from_text(str(part.get("text", "")))
            elif isinstance(part, str):
                total += _estimate_tokens_from_text(part)
        return total
    if isinstance(value, dict):
        return _estimate_tokens_from_text(str(value.get("text", "")))
    return _estimate_tokens_from_text(str(value))


def _estimate_request_tokens(req: Dict[str, Any]) -> BatchEstimateItem:
    params = req.get("params", {})
    model = str(params.get("model", "claude-sonnet-4-6"))
    max_tokens = int(params.get("max_tokens", 0) or 0)

    estimated_input = 0
    system_block = params.get("system")
    estimated_input += _extract_text_tokens(system_block)

    for msg in params.get("messages", []):
        estimated_input += _extract_text_tokens(msg.get("content"))

    context_window = _MODEL_CONTEXT_WINDOWS.get(model, 200_000)
    estimated_total = estimated_input + max_tokens
    remaining = context_window - estimated_total

    return BatchEstimateItem(
        custom_id=req.get("custom_id", ""),
        model=model,
        context_window_tokens=context_window,
        estimated_input_tokens=estimated_input,
        configured_max_tokens=max_tokens,
        estimated_total_tokens=estimated_total,
        remaining_context_tokens=max(0, remaining),
        is_over_context_limit=remaining < 0,
    )


@router.post(
    "/batch-submit",
    response_model=BatchStatusResponse,
    responses=_BATCH_ERROR_RESPONSES,
    summary="Submit a batch of Anthropic inference requests",
    description=(
        "Submit up to 100,000 Claude inference requests as a single batch. "
        "Anthropic processes them asynchronously at 50% of standard API cost. "
        "Returns a batch_id — use batch-poll to check when processing finishes."
    ),
)
async def batch_submit(
    body: BatchSubmitRequest = Body(
        ...,
        openapi_examples={
            "basic": {
                "summary": "Simple batch request",
                "description": (
                    "Single Claude inference request submitted as a batch. "
                    "After submission, polling runs automatically in the background every 60s. "
                    "Results are stored in PI schema automatically once processing_status is 'ended'. "
                    "Use batch-poll to check status manually or batch-fetch to retrieve results."
                ),
                "value": {
                    "requests": [
                        {
                            "custom_id": "req-001",
                            "params": {
                                "model": "claude-sonnet-4-6",
                                "max_tokens": 512,
                                "messages": [
                                    {
                                        "role": "user",
                                        "content": "What is machine learning? Explain in 3 sentences."
                                    }
                                ]
                            }
                        }
                    ]
                }
            },
            "prompt_caching": {
                "summary": "Batch with prompt caching (BP1–BP4)",
                "description": (
                    "Submit multiple requests sharing the same large document using Anthropic prompt caching. "
                    "BP1 and BP2 are system blocks cached for 1h — put large static documents here. "
                    "BP3 is a user content block cached for 5m — put per-company profile here. "
                    "BP4 is the unique per-request text with no cache — put the specific work item here. "
                    "First request creates the cache. All subsequent requests read from cache at ~90% cheaper cost. "
                    "Use POST /prompt-templates to create a reusable template and POST /prompt-templates/render "
                    "to generate this payload automatically from variable values."
                ),
                "value": {
                    "requests": [
                        {
                            "custom_id": "work-item-001",
                            "params": {
                                "model": "claude-sonnet-4-6",
                                "max_tokens": 1024,
                                "system": [
                                    {
                                        "type": "text",
                                        "text": "<BP1 — full regulation or policy document, must be 1024+ tokens>",
                                        "cache_control": {"type": "ephemeral"}
                                    },
                                    {
                                        "type": "text",
                                        "text": "<BP2 — evaluator instructions, shared across all requests>",
                                        "cache_control": {"type": "ephemeral"}
                                    }
                                ],
                                "messages": [
                                    {
                                        "role": "user",
                                        "content": [
                                            {
                                                "type": "text",
                                                "text": "<BP3 — company or regime profile, same per company>",
                                                "cache_control": {"type": "ephemeral"}
                                            },
                                            {
                                                "type": "text",
                                                "text": "<BP4 — specific work item unique to this request, no cache_control>"
                                            }
                                        ]
                                    }
                                ]
                            }
                        },
                        {
                            "custom_id": "work-item-002",
                            "params": {
                                "model": "claude-sonnet-4-6",
                                "max_tokens": 1024,
                                "system": [
                                    {
                                        "type": "text",
                                        "text": "<BP1 — exact same document as work-item-001>",
                                        "cache_control": {"type": "ephemeral"}
                                    },
                                    {
                                        "type": "text",
                                        "text": "<BP2 — exact same instructions as work-item-001>",
                                        "cache_control": {"type": "ephemeral"}
                                    }
                                ],
                                "messages": [
                                    {
                                        "role": "user",
                                        "content": [
                                            {
                                                "type": "text",
                                                "text": "<BP3 — exact same company profile as work-item-001>",
                                                "cache_control": {"type": "ephemeral"}
                                            },
                                            {
                                                "type": "text",
                                                "text": "<BP4 — different work item, only this changes>"
                                            }
                                        ]
                                    }
                                ]
                            }
                        }
                    ]
                }
            }
        }
    ),
    credentials: HTTPAuthorizationCredentials = Security(auth_scheme),
):
    token, identity = _get_identity(credentials)
    service = AnthropicBatchService()
    requests_payload = [item.model_dump(exclude_none=True) for item in body.requests]

    if body.vocabulary_id:
        vocab_service = VocabularyService()
        vocab = vocab_service.get(body.vocabulary_id)
        if not vocab:
            raise HTTPException(status_code=404, detail=f"Vocabulary not found: {body.vocabulary_id}")
        requests_payload = vocab_service.apply_to_requests(requests_payload, body.vocabulary_id)
        logger.info("Vocabulary applied | vocab_id=%s name=%s requests=%d", body.vocabulary_id, vocab["name"], len(requests_payload))

    result = await service.submit(requests_payload, identity, token)

    celery_app.send_task(
        "batch.poll_and_fetch",
        args=[result["batch_id"], token, identity, requests_payload],
        queue="batch",
    )

    logger.info("Batch submitted | batch_id=%s count=%d tenant=%s vocab=%s", result["batch_id"], len(body.requests), identity.get("tenantId"), body.vocabulary_id)
    return result


@router.post(
    "/prompt-templates",
    response_model=PromptTemplateCreateResponse,
    responses=_BATCH_ERROR_RESPONSES,
    summary="Create a reusable prompt template for batch prompt caching",
    description=(
        "Register a reusable template with large static blocks (document + instructions) and shared user blocks. "
        "Blocks are rendered into Anthropic prompt-caching format so you can submit one large context once and "
        "send many smaller BP4 work items."
    ),
)
async def create_prompt_template(
    body: PromptTemplateCreateRequest,
    credentials: HTTPAuthorizationCredentials = Security(auth_scheme),
):
    _, identity = _get_identity(credentials)
    service = PromptTemplateService()
    record = service.create(body)
    logger.info(
        "Prompt template created | template_id=%s name=%s tenant=%s",
        record["template_id"],
        body.name,
        identity.get("tenantId"),
    )
    return record


@router.post(
    "/prompt-templates/render",
    response_model=PromptTemplateRenderResponse,
    responses=_BATCH_ERROR_RESPONSES,
    summary="Render a template into batch-submit requests",
    description=(
        "Builds concrete batch requests by combining a stored template with per-item BP4 work items and variables. "
        "Use the returned requests as the payload for POST /v1/batch/batch-submit."
    ),
)
async def render_prompt_template(
    body: PromptTemplateRenderRequest,
    credentials: HTTPAuthorizationCredentials = Security(auth_scheme),
):
    _, identity = _get_identity(credentials)
    service = PromptTemplateService()
    rendered = service.render_requests(body.template_id, [item.model_dump() for item in body.items])
    if not rendered:
        raise HTTPException(
            status_code=404,
            detail={
                "error": "PROMPT_TEMPLATE_NOT_FOUND",
                "message": f"Prompt template not found or expired: {body.template_id}",
            },
        )

    logger.info(
        "Prompt template rendered | template_id=%s requests=%d tenant=%s",
        body.template_id,
        len(rendered),
        identity.get("tenantId"),
    )
    return {
        "template_id": body.template_id,
        "request_count": len(rendered),
        "requests": rendered,
    }


@router.post(
    "/batch-estimate",
    response_model=BatchEstimateResponse,
    responses=_BATCH_ERROR_RESPONSES,
    summary="Estimate tokens and remaining context for a batch payload",
    description=(
        "Pre-flight estimator for input tokens, configured output tokens, context-window usage, and remaining tokens "
        "per request. Uses a fast approximation (about 1 token per 4 characters)."
    ),
)
async def batch_estimate(
    body: BatchEstimateRequest,
    credentials: HTTPAuthorizationCredentials = Security(auth_scheme),
):
    _, identity = _get_identity(credentials)
    items = [_estimate_request_tokens(item.model_dump(exclude_none=True)) for item in body.requests]

    total_input = sum(i.estimated_input_tokens for i in items)
    total_output = sum(i.configured_max_tokens for i in items)
    total = sum(i.estimated_total_tokens for i in items)

    logger.info(
        "Batch estimated | requests=%d tenant=%s total_estimated_tokens=%d",
        len(items),
        identity.get("tenantId"),
        total,
    )
    return {
        "request_count": len(items),
        "total_estimated_input_tokens": total_input,
        "total_configured_max_output_tokens": total_output,
        "total_estimated_tokens": total,
        "items": items,
    }


@router.get(
    "/batch-poll/{batch_id}",
    response_model=BatchStatusResponse,
    responses=_BATCH_ERROR_RESPONSES,
    summary="Poll batch processing status",
    description=(
        "Check the current status of a submitted batch. "
        "Poll every 60 seconds until processing_status is 'ended'. "
        "Only call batch-fetch once status is 'ended'."
    ),
)
async def batch_poll(
    batch_id: str,
    credentials: HTTPAuthorizationCredentials = Security(auth_scheme),
):
    token, identity = _get_identity(credentials)
    service = AnthropicBatchService()
    result = await service.poll(batch_id)
    logger.info("Batch polled | batch_id=%s status=%s tenant=%s", batch_id, result["processing_status"], identity.get("tenantId"))
    return result


@router.get(
    "/batch-fetch/{batch_id}",
    response_model=BatchFetchWithUsageResponse,
    responses=_BATCH_ERROR_RESPONSES,
    summary="Fetch completed batch results",
    description=(
        "Retrieve all results for an ended batch. "
        "Returns 409 if the batch is still processing — poll first. "
        "Results may be in any order; match by custom_id. "
        "Each result has type: succeeded | errored | canceled | expired."
    ),
)
async def batch_fetch(
    batch_id: str,
    credentials: HTTPAuthorizationCredentials = Security(auth_scheme),
):
    token, identity = _get_identity(credentials)
    service = AnthropicBatchService()
    result = await service.fetch_results(batch_id, identity=identity, token=token)
    logger.info("Batch fetched | batch_id=%s total=%d tenant=%s", batch_id, result["total"], identity.get("tenantId"))
    return result


@router.post(
    "/batch-cancel/{batch_id}",
    response_model=BatchStatusResponse,
    responses=_BATCH_ERROR_RESPONSES,
    summary="Cancel an in-progress batch",
    description=(
        "Cancel a batch that is currently in_progress. "
        "Anthropic will stop processing remaining requests — already completed ones are unaffected. "
        "Status moves to 'canceling' then 'ended'. Must cancel before deleting."
    ),
)
async def batch_cancel(
    batch_id: str,
    credentials: HTTPAuthorizationCredentials = Security(auth_scheme),
):
    _, identity = _get_identity(credentials)
    service = AnthropicBatchService()
    result = await service.cancel(batch_id)
    logger.info("Batch cancelled | batch_id=%s tenant=%s", batch_id, identity.get("tenantId"))
    return result


@router.delete(
    "/batch-delete/{batch_id}",
    responses=_BATCH_ERROR_RESPONSES,
    summary="Delete a completed or cancelled batch",
    description=(
        "Permanently delete a batch and its results. "
        "Batch must be ended or cancelled first — returns 409 if still in_progress. "
        "Results are no longer accessible after deletion."
    ),
)
async def batch_delete(
    batch_id: str,
    credentials: HTTPAuthorizationCredentials = Security(auth_scheme),
):
    _, identity = _get_identity(credentials)
    service = AnthropicBatchService()
    result = await service.delete(batch_id)
    logger.info("Batch deleted | batch_id=%s tenant=%s", batch_id, identity.get("tenantId"))
    return result
