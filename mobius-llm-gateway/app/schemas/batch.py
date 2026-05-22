from pydantic import BaseModel, Field, field_validator
from typing import Any, Dict, List, Optional
from datetime import datetime


class BatchRequestParams(BaseModel):
    model: str
    max_tokens: int
    messages: List[Dict[str, Any]]
    system: Optional[Any] = None
    temperature: Optional[float] = None
    top_p: Optional[float] = None
    top_k: Optional[int] = None
    stop_sequences: Optional[List[str]] = None
    tools: Optional[List[Dict[str, Any]]] = None
    tool_choice: Optional[Dict[str, Any]] = None
    metadata: Optional[Dict[str, Any]] = None

    class Config:
        extra = "allow"


class BatchRequestItem(BaseModel):
    custom_id: str = Field(
        ...,
        pattern=r"^[a-zA-Z0-9_-]{1,64}$",
        description="Unique ID per request — 1-64 chars, alphanumeric / dash / underscore"
    )
    params: BatchRequestParams


class BatchSubmitRequest(BaseModel):
    requests: List[BatchRequestItem] = Field(..., description="List of requests (max 100,000 per batch)")
    vocabulary_id: Optional[str] = Field(
        None,
        description=(
            "Optional vocabulary ID to apply to all requests in this batch. "
            "Injects the vocabulary instructions into every request's system prompt. "
            "Create a vocabulary at POST /vocabulary. "
            "If a request also has its own vocabulary_id in params, that takes priority."
        )
    )

    @field_validator("requests")
    @classmethod
    def validate_request_count(cls, v):
        if len(v) == 0:
            raise ValueError("requests list cannot be empty")
        if len(v) > 100_000:
            raise ValueError("requests list cannot exceed 100,000 items")
        return v


class BatchRequestCounts(BaseModel):
    processing: int = 0
    succeeded: int = 0
    errored: int = 0
    canceled: int = 0
    expired: int = 0


class BatchStatusResponse(BaseModel):
    batch_id: str
    processing_status: str
    request_counts: BatchRequestCounts
    created_at: str
    expires_at: str
    ended_at: Optional[str] = None
    cancel_initiated_at: Optional[str] = None
    results_url: Optional[str] = None


class BatchResultItem(BaseModel):
    custom_id: str
    result: Dict[str, Any]


class BatchFetchResponse(BaseModel):
    batch_id: str
    total: int
    results: List[BatchResultItem]


class ApiErrorResponse(BaseModel):
    error: str
    message: str
    details: Optional[Dict[str, Any]] = None
    retry_after_seconds: Optional[int] = None


class PromptTemplateBlock(BaseModel):
    text: str = Field(..., min_length=1, description="Template text. Supports {{variable}} placeholders.")
    cache: bool = Field(True, description="If true, inject cache_control={type: ephemeral} for Anthropic prompt caching.")


class PromptTemplateCreateRequest(BaseModel):
    name: str = Field(..., min_length=2, max_length=120)
    model: str
    max_tokens: int = Field(..., gt=0)
    system_blocks: List[PromptTemplateBlock] = Field(default_factory=list)
    shared_user_blocks: List[PromptTemplateBlock] = Field(default_factory=list)
    expires_in_minutes: int = Field(180, ge=5, le=1440)
    metadata: Optional[Dict[str, Any]] = None


class PromptTemplateCreateResponse(BaseModel):
    template_id: str
    name: str
    model: str
    max_tokens: int
    created_at: datetime
    expires_at: datetime
    metadata: Optional[Dict[str, Any]] = None


class PromptTemplateRenderItem(BaseModel):
    custom_id: str = Field(..., pattern=r"^[a-zA-Z0-9_-]{1,64}$")
    work_item: str = Field(..., min_length=1, description="Unique per-request prompt block (BP4).")
    variables: Dict[str, Any] = Field(default_factory=dict, description="Variables used to render {{placeholders}}.")


class PromptTemplateRenderRequest(BaseModel):
    template_id: str
    items: List[PromptTemplateRenderItem] = Field(..., min_length=1, max_length=100_000)


class PromptTemplateRenderResponse(BaseModel):
    template_id: str
    request_count: int
    requests: List[BatchRequestItem]


class BatchEstimateRequest(BaseModel):
    requests: List[BatchRequestItem] = Field(..., min_length=1, max_length=100_000)


class BatchEstimateItem(BaseModel):
    custom_id: str
    model: str
    context_window_tokens: int
    estimated_input_tokens: int
    configured_max_tokens: int
    estimated_total_tokens: int
    remaining_context_tokens: int
    is_over_context_limit: bool


class BatchEstimateResponse(BaseModel):
    request_count: int
    total_estimated_input_tokens: int
    total_configured_max_output_tokens: int
    total_estimated_tokens: int
    items: List[BatchEstimateItem]


class BatchUsageSummary(BaseModel):
    total_input_tokens: int = 0
    total_output_tokens: int = 0
    total_tokens: int = 0
    total_cache_creation_tokens: int = 0
    total_cache_read_tokens: int = 0


class BatchFetchWithUsageResponse(BatchFetchResponse):
    usage_summary: BatchUsageSummary
