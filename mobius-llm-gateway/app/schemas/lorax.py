from pydantic import BaseModel, Field, field_validator, ConfigDict
from typing import Optional, List, Dict, Any, Literal, Union
from enum import Enum


# ============= Enums =============
class AdapterSource(str, Enum):
    """LoRAX adapter source types"""
    HUB = "hub"
    LOCAL = "local"
    S3 = "s3"


class MessageRole(str, Enum):
    """Chat message roles"""
    SYSTEM = "system"
    USER = "user"
    ASSISTANT = "assistant"


class BaseSchema(BaseModel):
    model_config = ConfigDict(extra="forbid", protected_namespaces=())


# ============= Common Models =============
class AdapterConfig(BaseSchema):
    """LoRAX adapter configuration"""
    id: str = Field(..., description="Adapter ID")
    weight: Optional[float] = Field(1.0, ge=0.0, le=2.0, description="Adapter weight (0.0-2.0)")


class BaseLoRAXRequest(BaseSchema):
    """Base request model with common LoRAX fields"""
    deployment_id: Optional[str] = Field(None, description="Deployment ID for LoRAX service (provide this or base_url in request)")
    base_url: Optional[str] = Field(None, description="Direct base URL of LoRAX service (alternative to deployment_id)")
    agent_id: Optional[str] = Field(None, description="Agent ID — optional, null when not provided")
    adapter_id: Optional[str] = Field(None, description="LoRA adapter ID to use")
    adapter_source: Optional[AdapterSource] = Field(None, description="Adapter source (hub/local/s3)")
    merged_adapters: Optional[Dict[str, float]] = Field(
        None, 
        description="Multiple adapters with weights, e.g., {'adapter1': 0.5, 'adapter2': 0.5}"
    )
    api_token: Optional[str] = Field(None, description="API token for private adapters")

    @field_validator('deployment_id', 'base_url', 'agent_id', 'adapter_id', mode='before')
    @classmethod
    def empty_string_to_none(cls, v):
        return None if v == "" else v

    @field_validator('merged_adapters')
    @classmethod
    def validate_merged_adapters(cls, v):
        """Ensure merged adapter weights are valid"""
        if v is not None:
            for adapter_id, weight in v.items():
                if not (0.0 <= weight <= 2.0):
                    raise ValueError(f"Adapter weight for '{adapter_id}' must be between 0.0 and 2.0")
        return v


# ============= Generation Parameters =============
class GenerateParameters(BaseSchema):
    """Parameters for text generation"""
    do_sample: Optional[bool] = Field(False, description="Activate logits sampling")
    max_new_tokens: Optional[int] = Field(20, ge=1, le=4096, description="Maximum number of tokens to generate")
    repetition_penalty: Optional[float] = Field(None, ge=0.0, le=2.0, description="Repetition penalty (1.0 = no penalty)")
    return_full_text: Optional[bool] = Field(False, description="Return full text including prompt")
    stop: Optional[List[str]] = Field(None, max_length=10, description="Stop sequences (max 10)")
    seed: Optional[int] = Field(None, ge=0, description="Random seed for reproducibility")
    temperature: Optional[float] = Field(None, ge=0.0, le=2.0, description="Sampling temperature (0.0-2.0)")
    top_k: Optional[int] = Field(None, ge=0, le=100, description="Top-k sampling (0-100)")
    top_p: Optional[float] = Field(None, ge=0.0, le=1.0, description="Top-p (nucleus) sampling (0.0-1.0)")
    truncate: Optional[int] = Field(None, ge=0, description="Truncate input tokens to this length")
    typical_p: Optional[float] = Field(None, ge=0.0, le=1.0, description="Typical sampling probability")
    watermark: Optional[bool] = Field(False, description="Watermark generated text")
    best_of: Optional[int] = Field(None, ge=1, le=10, description="Generate best_of completions and return the best")
    
    @field_validator('stop')
    @classmethod
    def validate_stop_sequences(cls, v):
        """Validate stop sequences"""
        if v is not None:
            if len(v) > 10:
                raise ValueError("Maximum 10 stop sequences allowed")
            if any(len(seq) == 0 for seq in v):
                raise ValueError("Stop sequences cannot be empty strings")
        return v


# ============= Generate (Root Endpoint) =============
class GenerateRootRequest(BaseModel):
    model_config = ConfigDict(
        extra="allow",
        json_schema_extra={
            "examples": [
                {
                    "deployment_id": "lorax-dep-789",
                    "inputs": "What is the future of AI?",
                    "parameters": {"max_new_tokens": 100, "temperature": 0.7}
                },
                {
                    "base_url": "http://lorax-service:80",
                    "inputs": "Explain neural networks in simple terms.",
                    "parameters": {"max_new_tokens": 150}
                }
            ]
        }
    )
    deployment_id: Optional[str] = Field(None, description="Deployment ID (provide this or base_url)")
    base_url: Optional[str] = Field(None, description="Direct base URL of LoRAX service")
    agent_id: Optional[str] = Field(None, description="Agent ID — optional, null when not provided")
    inputs: str = Field(..., min_length=1, description="Input text")

    @field_validator('deployment_id', 'base_url', 'agent_id', mode='before')
    @classmethod
    def empty_string_to_none(cls, v):
        return None if v == "" else v

class GenerateRootResponse(BaseSchema):
    generated_text: str
    details: Optional[Dict[str, Any]] = None


# ============= Generate =============
class GenerateRequest(BaseLoRAXRequest):
    """Request model for /generate endpoint"""
    model_config = ConfigDict(
        extra="forbid",
        json_schema_extra={
            "examples": [
                {
                    "deployment_id": "lorax-dep-789",
                    "inputs": "What is the future of AI?",
                    "adapter_id": "my-lora-adapter",
                    "parameters": {"max_new_tokens": 100, "temperature": 0.7}
                },
                {
                    "base_url": "http://lorax-service:80",
                    "inputs": "Explain transformers briefly.",
                    "parameters": {"max_new_tokens": 80}
                }
            ]
        }
    )
    inputs: str = Field(..., min_length=1, description="Input text to generate from")
    parameters: Optional[GenerateParameters] = Field(None, description="Structured generation parameters")


class TokenDetails(BaseSchema):
    """Details about a generated token"""
    id: int = Field(..., description="Token ID")
    text: str = Field(..., description="Token text")
    logprob: Optional[float] = Field(None, description="Log probability")
    special: bool = Field(False, description="Whether token is special")


class GenerationDetails(BaseSchema):
    """Detailed information about the generation"""
    finish_reason: str = Field(..., description="Why generation stopped (length/eos_token/stop_sequence)")
    generated_tokens: int = Field(..., description="Number of tokens generated")
    seed: Optional[int] = Field(None, description="Seed used for generation")
    prefill: Optional[List[TokenDetails]] = Field(None, description="Prefill token details")
    tokens: Optional[List[TokenDetails]] = Field(None, description="Generated token details")


class GenerateResponse(BaseSchema):
    """Response model for /generate endpoint"""
    generated_text: str = Field(..., description="Generated text")
    details: Optional[GenerationDetails] = Field(None, description="Detailed generation information")


# ============= Generate Stream =============
class GenerateStreamRequest(BaseLoRAXRequest):
    """Request model for streaming generation"""
    model_config = ConfigDict(
        extra="forbid",
        json_schema_extra={
            "examples": [
                {
                    "deployment_id": "lorax-dep-789",
                    "inputs": "Tell me a story about AI.",
                    "parameters": {"max_new_tokens": 200, "temperature": 0.8}
                },
                {
                    "base_url": "http://lorax-service:80",
                    "inputs": "Write a poem about machine learning.",
                    "parameters": {"max_new_tokens": 150}
                }
            ]
        }
    )
    inputs: str = Field(..., min_length=1, description="Input text to generate from")
    parameters: Optional[GenerateParameters] = Field(None, description="Generation parameters")


class GenerateStreamResponse(BaseSchema):
    """Individual stream response chunk"""
    token: TokenDetails = Field(..., description="Current token details")
    generated_text: Optional[str] = Field(None, description="Full generated text (only in final chunk)")
    details: Optional[GenerationDetails] = Field(None, description="Generation details (only in final chunk)")


# ============= Chat Completions (OpenAI Compatible) =============
class ChatMessage(BaseSchema):
    """Chat message in OpenAI format"""
    role: MessageRole = Field(..., description="Message role")
    content: str = Field(..., min_length=1, description="Message content")
    name: Optional[str] = Field(None, description="Optional name of the message author")


class ChatCompletionRequest(BaseLoRAXRequest):
    """OpenAI-compatible chat completion request"""
    model_config = ConfigDict(
        extra="forbid",
        json_schema_extra={
            "examples": [
                {
                    "deployment_id": "lorax-dep-789",
                    "model": "mistralai/Mistral-7B-Instruct-v0.1",
                    "messages": [
                        {"role": "user", "content": "Explain neural networks."}
                    ],
                    "adapter_id": "my-custom-lora",
                    "max_tokens": 256,
                    "temperature": 0.7
                },
                {
                    "base_url": "http://lorax-service:80",
                    "model": "mistralai/Mistral-7B-Instruct-v0.1",
                    "messages": [
                        {"role": "system", "content": "You are a helpful assistant."},
                        {"role": "user", "content": "What is LoRA fine-tuning?"}
                    ],
                    "max_tokens": 200
                }
            ]
        }
    )
    model: str = Field(..., description="Model name (required by OpenAI API spec)")
    messages: List[ChatMessage] = Field(..., min_length=1, description="Chat messages")
    temperature: Optional[float] = Field(1.0, ge=0.0, le=2.0, description="Sampling temperature")
    top_p: Optional[float] = Field(1.0, ge=0.0, le=1.0, description="Nucleus sampling")
    n: Optional[int] = Field(1, ge=1, le=10, description="Number of completions to generate")
    max_tokens: Optional[int] = Field(None, ge=1, le=4096, description="Maximum tokens to generate")
    stop: Optional[Union[str, List[str]]] = Field(None, description="Stop sequences (string or list)")
    stream: Optional[bool] = Field(False, description="Stream responses using SSE")
    presence_penalty: Optional[float] = Field(0.0, ge=-2.0, le=2.0, description="Presence penalty")
    frequency_penalty: Optional[float] = Field(0.0, ge=-2.0, le=2.0, description="Frequency penalty")
    logit_bias: Optional[Dict[str, float]] = Field(None, description="Token logit bias")
    user: Optional[str] = Field(None, description="Unique user identifier")
    
    @field_validator('messages')
    @classmethod
    def validate_messages(cls, v):
        """Ensure messages list is not empty and has valid structure"""
        if not v:
            raise ValueError("Messages list cannot be empty")
        return v


class ChatChoice(BaseSchema):
    """Chat completion choice"""
    index: int = Field(..., description="Choice index")
    message: ChatMessage = Field(..., description="Generated message")
    finish_reason: Optional[str] = Field(None, description="Why generation stopped")


class UsageInfo(BaseSchema):
    """Token usage information"""
    prompt_tokens: int = Field(..., ge=0, description="Tokens in the prompt")
    completion_tokens: int = Field(..., ge=0, description="Tokens in the completion")
    total_tokens: int = Field(..., ge=0, description="Total tokens used")


class ChatCompletionResponse(BaseSchema):
    """OpenAI-compatible chat completion response"""
    id: str = Field(..., description="Unique completion ID")
    object: str = Field(default="chat.completion", description="Object type")
    created: int = Field(..., description="Unix timestamp")
    model: str = Field(..., description="Model used")
    choices: List[ChatChoice] = Field(..., description="List of completion choices")
    usage: UsageInfo = Field(..., description="Token usage statistics")


# ============= Completions (OpenAI Compatible) =============
class CompletionRequest(BaseLoRAXRequest):
    """OpenAI-compatible text completion request"""
    model_config = ConfigDict(
        extra="forbid",
        json_schema_extra={
            "examples": [
                {
                    "deployment_id": "lorax-dep-789",
                    "model": "mistralai/Mistral-7B-v0.1",
                    "prompt": "The future of technology is",
                    "max_tokens": 150,
                    "temperature": 0.8
                },
                {
                    "base_url": "http://lorax-service:80",
                    "model": "mistralai/Mistral-7B-v0.1",
                    "prompt": "Once upon a time",
                    "max_tokens": 100
                }
            ]
        }
    )
    model: str = Field(..., description="Model name (required by OpenAI API spec)")
    prompt: Union[str, List[str]] = Field(..., description="Prompt(s) to generate from")
    temperature: Optional[float] = Field(1.0, ge=0.0, le=2.0, description="Sampling temperature")
    top_p: Optional[float] = Field(1.0, ge=0.0, le=1.0, description="Nucleus sampling")
    n: Optional[int] = Field(1, ge=1, le=10, description="Number of completions to generate")
    max_tokens: Optional[int] = Field(16, ge=1, le=4096, description="Maximum tokens to generate")
    stop: Optional[Union[str, List[str]]] = Field(None, description="Stop sequences")
    stream: Optional[bool] = Field(False, description="Stream responses using SSE")
    presence_penalty: Optional[float] = Field(0.0, ge=-2.0, le=2.0, description="Presence penalty")
    frequency_penalty: Optional[float] = Field(0.0, ge=-2.0, le=2.0, description="Frequency penalty")
    logit_bias: Optional[Dict[str, float]] = Field(None, description="Token logit bias")
    user: Optional[str] = Field(None, description="Unique user identifier")
    best_of: Optional[int] = Field(None, ge=1, le=20, description="Generate best_of completions")
    logprobs: Optional[int] = Field(None, ge=0, le=5, description="Include top N log probabilities")
    echo: Optional[bool] = Field(False, description="Echo the prompt in the output")
    suffix: Optional[str] = Field(None, description="Text to append after completion")
    
    @field_validator('prompt')
    @classmethod
    def validate_prompt(cls, v):
        """Ensure prompt is not empty"""
        if isinstance(v, str) and len(v) == 0:
            raise ValueError("Prompt cannot be empty")
        if isinstance(v, list) and (len(v) == 0 or any(len(p) == 0 for p in v)):
            raise ValueError("Prompt list cannot be empty and cannot contain empty strings")
        return v


class CompletionChoice(BaseSchema):
    """Text completion choice"""
    index: int = Field(..., description="Choice index")
    text: str = Field(..., description="Generated text")
    finish_reason: Optional[str] = Field(None, description="Why generation stopped")
    logprobs: Optional[Dict[str, Any]] = Field(None, description="Log probabilities if requested")


class CompletionResponse(BaseSchema):
    """OpenAI-compatible text completion response"""
    id: str = Field(..., description="Unique completion ID")
    object: str = Field(default="text_completion", description="Object type")
    created: int = Field(..., description="Unix timestamp")
    model: str = Field(..., description="Model used")
    choices: List[CompletionChoice] = Field(..., description="List of completion choices")
    usage: UsageInfo = Field(..., description="Token usage statistics")


# ============= Health Check =============
class HealthResponse(BaseSchema):
    """Health check response"""
    status: str = Field(..., description="Health status (healthy/unhealthy)")
    deployment_id: str = Field(..., description="Deployment ID")
    service_name: str = Field(..., description="Service name")
    timestamp: Optional[str] = Field(None, description="Timestamp of health check")


# ============= Info =============
class InfoResponse(BaseSchema):
    """LoRAX service information response"""
    model_id: str = Field(..., description="Model identifier")
    model_dtype: str = Field(..., description="Model data type (e.g., float16, bfloat16)")
    model_device_type: str = Field(..., description="Device type (e.g., cuda, cpu)")
    model_pipeline_tag: Optional[str] = Field(None, description="Pipeline tag")
    max_concurrent_requests: int = Field(..., ge=1, description="Maximum concurrent requests")
    max_input_length: int = Field(..., ge=1, description="Maximum input length in tokens")
    max_total_tokens: int = Field(..., ge=1, description="Maximum total tokens (input + output)")
    waiting_served_ratio: float = Field(..., ge=0.0, description="Waiting to served ratio")
    max_batch_size: Optional[int] = Field(None, ge=1, description="Maximum batch size")
    max_waiting_tokens: int = Field(..., ge=0, description="Maximum waiting tokens")
    validation_workers: int = Field(..., ge=1, description="Number of validation workers")
    version: str = Field(..., description="LoRAX version")
    deployment_id: str = Field(..., description="Deployment ID")
    service_name: str = Field(..., description="Service name")


# ============= Metrics =============
class MetricsResponse(BaseSchema):
    """Prometheus metrics response"""
    metrics: str = Field(..., description="Prometheus-format metrics text")
    deployment_id: str = Field(..., description="Deployment ID")
    service_name: str = Field(..., description="Service name")
    timestamp: Optional[str] = Field(None, description="Timestamp when metrics were collected")


# ============= Error Response =============
class ErrorDetail(BaseSchema):
    """Error detail model"""
    error: str = Field(..., description="Error code")
    message: str = Field(..., description="Human-readable error message")
    details: Optional[Dict[str, Any]] = Field(None, description="Additional error details")


class ErrorResponse(BaseSchema):
    """Standard error response"""
    detail: Union[str, ErrorDetail, Dict[str, Any]] = Field(..., description="Error details")
    status_code: int = Field(..., description="HTTP status code")