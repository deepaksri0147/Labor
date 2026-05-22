# In app/model/vllm.py

from pydantic import BaseModel, ConfigDict, field_validator
from typing import List, Optional, Dict, Any, Union

class BaseSchema(BaseModel):
    model_config = ConfigDict(extra="forbid")

# ============= Completion Models =============

class CompletionLogprobs(BaseSchema):
    text_offset: Optional[List[int]] = None
    token_logprobs: Optional[List[float]] = None
    tokens: Optional[List[str]] = None
    top_logprobs: Optional[List[Dict[str, float]]] = None

class CompletionChoice(BaseSchema):
    text: str
    index: int
    logprobs: Optional[CompletionLogprobs] = None
    finish_reason: Optional[str] = None

class UsageInfo(BaseSchema):
    prompt_tokens: int
    completion_tokens: int
    total_tokens: int
    prompt_tokens_details: Optional[int] = None

class CompletionRequest(BaseSchema):
    model_config = ConfigDict(
        extra="forbid",
        json_schema_extra={
            "examples": [
                {
                    "deployment_id": "vllm-dep-456",
                    "model": "meta-llama/Llama-3-8b-hf",
                    "prompt": "Once upon a time in a distant galaxy",
                    "max_tokens": 100,
                    "temperature": 0.8
                },
                {
                    "base_url": "http://vllm-service:8000",
                    "model": "meta-llama/Llama-3-8b-hf",
                    "prompt": "The future of AI is",
                    "max_tokens": 100,
                    "temperature": 0.7
                }
            ]
        }
    )
    deployment_id: Optional[str] = None
    base_url: Optional[str] = None
    agent_id: Optional[str] = None
    model: str
    prompt: Union[str, List[str]]
    max_tokens: Optional[int] = 16
    temperature: Optional[float] = 1.0
    top_p: Optional[float] = 1.0
    n: Optional[int] = 1
    stream: Optional[bool] = False
    logprobs: Optional[int] = None
    echo: Optional[bool] = False
    stop: Optional[Union[str, List[str]]] = None
    presence_penalty: Optional[float] = 0.0
    frequency_penalty: Optional[float] = 0.0
    best_of: Optional[int] = None
    logit_bias: Optional[Dict[str, float]] = None
    user: Optional[str] = None

    @field_validator('deployment_id', 'base_url', 'agent_id', mode='before')
    @classmethod
    def empty_string_to_none(cls, v):
        return None if v == "" else v

class CompletionResponse(BaseSchema):
    id: str
    object: str
    created: int
    model: str
    choices: List[CompletionChoice]
    usage: UsageInfo

# ============= Chat Completion Models =============

class ImageUrlDetail(BaseSchema):
    url: str
    detail: Optional[str] = "auto"

class VisionContentBlock(BaseSchema):
    model_config = ConfigDict(extra="forbid")
    type: str
    text: Optional[str] = None
    image_url: Optional[ImageUrlDetail] = None

class ChatMessage(BaseSchema):
    model_config = ConfigDict(extra="forbid")
    role: str
    content: Union[str, List[VisionContentBlock]]

class ChatCompletionChoice(BaseSchema):
    index: int
    message: ChatMessage
    finish_reason: Optional[str] = None
    logprobs: Optional[Dict[str, Any]] = None

class ChatCompletionRequest(BaseSchema):
    model_config = ConfigDict(
        extra="forbid",
        json_schema_extra={
            "examples": [
                {
                    "deployment_id": "vllm-dep-456",
                    "model": "meta-llama/Llama-3-8b-chat-hf",
                    "messages": [
                        {"role": "system", "content": "You are a helpful assistant."},
                        {"role": "user", "content": "Tell me about deep learning."}
                    ],
                    "temperature": 0.7,
                    "max_tokens": 512
                },
                {
                    "base_url": "http://vllm-service:8000",
                    "model": "meta-llama/Llama-3-8b-chat-hf",
                    "messages": [
                        {"role": "user", "content": "Summarize transformers architecture."}
                    ],
                    "max_tokens": 256
                }
            ]
        }
    )
    deployment_id: Optional[str] = None
    base_url: Optional[str] = None
    agent_id: Optional[str] = None
    model: str
    messages: List[ChatMessage]
    max_tokens: Optional[int] = None
    temperature: Optional[float] = 1.0
    top_p: Optional[float] = 1.0
    n: Optional[int] = 1
    stream: Optional[bool] = False
    stop: Optional[Union[str, List[str]]] = None
    presence_penalty: Optional[float] = 0.0
    frequency_penalty: Optional[float] = 0.0
    logit_bias: Optional[Dict[str, int]] = None
    user: Optional[str] = None

    @field_validator('deployment_id', 'base_url', 'agent_id', mode='before')
    @classmethod
    def empty_string_to_none(cls, v):
        return None if v == "" else v

class ChatCompletionResponse(BaseSchema):
    id: str
    object: str
    created: int
    model: str
    choices: List[ChatCompletionChoice]
    usage: UsageInfo

# ============= Other Models =============

class HealthResponse(BaseSchema):
    status: str
    deployment_id: Optional[str] = None
    service_name: Optional[str] = None

class TokenizeRequest(BaseSchema):
    model_config = ConfigDict(
        extra="forbid",
        json_schema_extra={
            "examples": [
                {
                    "deployment_id": "vllm-dep-456",
                    "model": "meta-llama/Llama-3-8b-hf",
                    "prompt": "Hello, how are you?"
                }
            ]
        }
    )
    deployment_id: Optional[str] = None
    base_url: Optional[str] = None
    agent_id: Optional[str] = None
    model: str
    prompt: str

    @field_validator('deployment_id', 'base_url', 'agent_id', mode='before')
    @classmethod
    def empty_string_to_none(cls, v):
        return None if v == "" else v

class TokenizeResponse(BaseSchema):
    tokens: List[int]
    count: int
    model: str

class DetokenizeRequest(BaseSchema):
    model_config = ConfigDict(
        extra="forbid",
        json_schema_extra={
            "examples": [
                {
                    "deployment_id": "vllm-dep-456",
                    "model": "meta-llama/Llama-3-8b-hf",
                    "tokens": [15043, 29892, 920, 526, 366, 29973]
                }
            ]
        }
    )
    deployment_id: Optional[str] = None
    base_url: Optional[str] = None
    agent_id: Optional[str] = None
    model: str
    tokens: List[int]

    @field_validator('deployment_id', 'base_url', 'agent_id', mode='before')
    @classmethod
    def empty_string_to_none(cls, v):
        return None if v == "" else v

class DetokenizeResponse(BaseSchema):
    prompt: str
    model: str

class ModelInfo(BaseSchema):
    id: str
    object: str
    created: int
    owned_by: str

class ModelsListResponse(BaseSchema):
    object: str
    data: List[ModelInfo]

class VersionResponse(BaseSchema):
    version: str
    deployment_id: Optional[str] = None
    service_name: Optional[str] = None

class EmbeddingData(BaseSchema):
    object: str
    embedding: List[float]
    index: int

class EmbeddingRequest(BaseSchema):
    model_config = ConfigDict(
        extra="forbid",
        json_schema_extra={
            "examples": [
                {
                    "deployment_id": "vllm-dep-456",
                    "model": "sentence-transformers/all-MiniLM-L6-v2",
                    "input": ["Text to embed", "Another text to embed"]
                },
                {
                    "base_url": "http://vllm-service:8000",
                    "model": "sentence-transformers/all-MiniLM-L6-v2",
                    "input": "Single text to embed"
                }
            ]
        }
    )
    deployment_id: Optional[str] = None
    base_url: Optional[str] = None
    agent_id: Optional[str] = None
    model: str
    input: Union[str, List[str]]
    encoding_format: Optional[str] = "float"
    user: Optional[str] = None

    @field_validator('deployment_id', 'base_url', 'agent_id', mode='before')
    @classmethod
    def empty_string_to_none(cls, v):
        return None if v == "" else v

class EmbeddingResponse(BaseSchema):
    object: str
    data: List[EmbeddingData]
    model: str
    usage: UsageInfo