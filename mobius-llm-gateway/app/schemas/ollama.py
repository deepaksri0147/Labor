from pydantic import BaseModel, Field, ConfigDict, field_validator
from typing import Optional, List, Dict, Any, Literal
from datetime import datetime

class BaseSchema(BaseModel):
    model_config = ConfigDict(extra="forbid", protected_namespaces=())

class GenerateRequest(BaseSchema):
    model_config = ConfigDict(
        extra="forbid",
        json_schema_extra={
            "examples": [
                {
                    "model": "llama3.2",
                    "prompt": "Why is the sky blue?",
                    "session_id": "session-abc123"
                }
            ]
        }
    )
    deployment_id: Optional[str] = Field(None, description="Deployment ID — optional, null when not provided")
    agent_id: Optional[str] = Field(None, description="Agent ID — optional, null when not provided")
    model: str = Field(..., description="Model name to use")
    prompt: str = Field(..., description="Prompt to generate from")
    stream: bool = Field(..., description="When true, returns NDJSON chunks as they arrive; when false, returns a single JSON response")
    session_id: Optional[str] = Field(None, description="Session ID for tracking (auto-generated if omitted)")

    @field_validator('deployment_id', 'agent_id', mode='before')
    @classmethod
    def empty_string_to_none(cls, v):
        return None if v == "" else v


class TokenUsage(BaseSchema):
    input_tokens: int = 0
    output_tokens: int = 0
    total_tokens: int = 0


class GenerateResponse(BaseSchema):
    status_code: int
    result: Dict[str, Any]
    usage: Optional[TokenUsage] = None
    session_id: str 

class ChatMessage(BaseSchema):
    role: Literal["system", "user", "assistant"]
    content: str


class ChatRequest(BaseSchema):
    model_config = ConfigDict(
        extra="forbid",
        json_schema_extra={
            "examples": [
                {
                    "model": "llama3.2",
                    "messages": [
                        {"role": "system", "content": "You are a helpful assistant."},
                        {"role": "user", "content": "What is machine learning?"}
                    ],
                    "session_id": "session-abc123"
                }
            ]
        }
    )
    deployment_id: Optional[str] = Field(None, description="Deployment ID — optional, null when not provided")
    agent_id: Optional[str] = Field(None, description="Agent ID — optional, null when not provided")
    model: str = Field(..., description="Model name to use")
    messages: List[ChatMessage] = Field(..., description="Chat messages")
    stream: bool = Field(..., description="When true, returns NDJSON chunks as they arrive; when false, returns a single JSON response")
    session_id: Optional[str] = Field(None, description="Session ID for tracking (auto-generated if omitted)")

    @field_validator('deployment_id', 'agent_id', mode='before')
    @classmethod
    def empty_string_to_none(cls, v):
        return None if v == "" else v


class ChatResponse(BaseSchema):
    status_code: int
    result: Dict[str, Any]
    usage: Optional[TokenUsage] = None

class EmbeddingsRequest(BaseSchema):
    model_config = ConfigDict(
        extra="forbid",
        json_schema_extra={
            "examples": [
                {
                    "model": "nomic-embed-text",
                    "input": "The quick brown fox jumps over the lazy dog",
                    "session_id": "session-abc123"
                }
            ]
        }
    )
    deployment_id: Optional[str] = Field(None, description="Deployment ID — optional, null when not provided")
    agent_id: Optional[str] = Field(None, description="Agent ID — optional, null when not provided")
    model: str = Field(..., description="Model name to use")
    input: str | List[str] = Field(..., description="Text to generate embeddings for")
    session_id: str = Field(..., description="Session ID for tracking")

    @field_validator('deployment_id', 'agent_id', mode='before')
    @classmethod
    def empty_string_to_none(cls, v):
        return None if v == "" else v


class EmbeddingsResponse(BaseSchema):
    status_code: int
    result: Dict[str, Any]


class ModelListResponse(BaseSchema):
    status_code: int
    result: Dict[str, Any]


class RunningModelsResponse(BaseSchema):
    status_code: int
    result: Dict[str, Any]


class ShowModelRequest(BaseSchema):
    model_config = ConfigDict(
        extra="forbid",
        json_schema_extra={"examples": [{"name": "llama3.2", "verbose": False}]}
    )
    name: str = Field(..., description="Model name")
    verbose: Optional[bool] = Field(None, description="Show verbose details")


class ShowModelResponse(BaseSchema):
    status_code: int
    result: Dict[str, Any]


class CreateModelRequest(BaseSchema):
    model_config = ConfigDict(
        extra="forbid",
        json_schema_extra={
            "examples": [
                {
                    "from_model": "llama3.2",
                    "model": "my-custom-llama",
                    "system": "You are a helpful coding assistant."
                }
            ]
        }
    )
    from_model: str = Field(..., description="Existing model to create from")
    model: str = Field(..., description="Name for the model to create")
    system: str = Field(False, description="System prompt to embed in the model")


class CreateModelResponse(BaseSchema):
    status_code: int
    result: Dict[str, Any]


class CopyModelRequest(BaseSchema):
    model_config = ConfigDict(
        extra="forbid",
        json_schema_extra={"examples": [{"source": "llama3.2", "destination": "llama3.2-backup"}]}
    )
    source: str = Field(..., description="Source model name")
    destination: str = Field(..., description="Destination model name")


class CopyModelResponse(BaseSchema):
    status_code: int
    result: str


class PullModelRequest(BaseSchema):
    model_config = ConfigDict(
        extra="forbid",
        json_schema_extra={"examples": [{"name": "llama3.2", "stream": False}]}
    )
    name: str = Field(..., description="Model name to pull")
    insecure: Optional[bool] = Field(None, description="Allow insecure connections")
    stream: bool = Field(False, description="Stream response")
    session_id: Optional[str] = None


class PullModelResponse(BaseSchema):
    session_id: str
    model_name: str
    status: str
    message: str
    timestamp: str


class PushModelRequest(BaseSchema):
    model_config = ConfigDict(
        extra="forbid",
        json_schema_extra={"examples": [{"name": "myuser/my-custom-llama", "stream": False}]}
    )
    name: str = Field(..., description="Model name to push")
    insecure: Optional[bool] = Field(None, description="Allow insecure connections")
    stream: bool = Field(False, description="Stream response")


class PushModelResponse(BaseSchema):
    status_code: int
    result: Dict[str, Any]


class DeleteModelRequest(BaseSchema):
    name: str = Field(..., description="Model name to delete")


class DeleteModelResponse(BaseSchema):
    status_code: int
    result: str


class VersionResponse(BaseSchema):
    status_code: int
    result: Dict[str, Any]