from pydantic import BaseModel, Field, field_validator, ConfigDict
from typing import Dict, Any, Union, Literal, Optional, List
from enum import Enum

# ============= Enums for Endpoints =============

class ToolType(str, Enum):
    """Available tools"""
    OLLAMA = "ollama"
    VLLM = "vllm"
    LORAX = "lorax"
    ONNX = "onnx"
    ARCTIC = "arctic"
    ANTHROPIC = "anthropic"
    GEMINI = "gemini"
    OPENAI = "openai"


class OllamaEndpoint(str, Enum):
    """Available endpoints for Ollama service"""
    GENERATE = "generate"
    CHAT = "chat"
    EMBEDDINGS = "embeddings"


class VLLMEndpoint(str, Enum):
    """Available endpoints for vLLM service"""
    CHAT_COMPLETIONS = "chat_completions"
    COMPLETIONS = "completions"
    EMBEDDINGS = "embeddings"


class LoRAXEndpoint(str, Enum):
    """Available endpoints for LoRAX service"""
    GENERATE_ROOT = "generate_root"
    GENERATE = "generate"
    GENERATE_STREAM = "generate_stream"
    CHAT_COMPLETIONS = "chat_completions"
    COMPLETIONS = "completions"


class ONNXEndpoint(str, Enum):
    """Available endpoints for ONNX service"""
    INFER = "infer"


# ============= Enums for Models (Dropdowns) =============

class ModelType(str, Enum):
    """Available models as dropdown options"""
    # Open-source — deploy via vLLM / LoRAX / Ollama
    LLAMA_31_8B = "meta-llama/Meta-Llama-3.1-8B-Instruct"
    LLAMA_33_70B = "meta-llama/Llama-3.3-70B-Instruct"
    QWEN3_8B = "Qwen/Qwen3-8B"
    QWEN3_32B = "Qwen/Qwen3-32B"
    PHI4_MINI = "microsoft/Phi-4-mini-instruct"
    MIXTRAL_8X22B = "mistralai/Mixtral-8x22B-Instruct-v0.1"
    DEEPSEEK_R1 = "deepseek-ai/DeepSeek-R1"
    # Vision — deploy via vLLM
    QWEN25_VL_72B = "Qwen/Qwen2.5-VL-72B-Instruct"
    # Embeddings — deploy via vLLM / Ollama
    QWEN3_EMBEDDING_8B = "Qwen/Qwen3-Embedding-8B"
    ALL_MINILM_L6_V2 = "sentence-transformers/all-MiniLM-L6-v2"
    # Cloud — via anthropic / gemini / openai tool
    CLAUDE_OPUS = "claude-opus-4-7"
    GPT5 = "gpt-5"
    GEMINI_25_PRO = "gemini-2.5-pro"
    # Legacy
    TINY_LLAMA = "TinyLlama/TinyLlama-1.1B-Chat-v1.0"
    SNOWFLAKE_ARCTIC = "Snowflake/snowflake-arctic-instruct"
    QWEN_05B = "Qwen/Qwen2.5-0.5B"
    LLAMA2 = "llama2"
    MISTRAL = "mistral"


# ============= Structured Input Models =============

class ImageUrl(BaseModel):
    url: str = Field(..., description="Image URL or base64 data URI — data:image/jpeg;base64,...")
    detail: Optional[Literal["auto", "low", "high"]] = Field("auto", description="Image detail level")


class ContentBlock(BaseModel):
    type: Literal["text", "image_url"] = Field(..., description="Content block type")
    text: Optional[str] = Field(None, description="Text content (when type=text)")
    image_url: Optional[ImageUrl] = Field(None, description="Image content (when type=image_url)")


class Message(BaseModel):
    role: str = Field(..., examples=["user", "assistant"])
    content: Union[str, List[ContentBlock]] = Field(..., examples=["Hello!"])


class BaseInferenceInput(BaseModel):
    model: Union[ModelType, str] = Field(..., description="Model ID (select from dropdown or enter custom)")
    max_tokens: Optional[int] = Field(128, description="Maximum tokens to generate")
    temperature: Optional[float] = Field(0.7, description="Sampling temperature")


class ChatInput(BaseInferenceInput):
    messages: list[Message] = Field(..., description="List of messages for chat completion")


class GenerateInput(BaseInferenceInput):
    prompt: str = Field(..., description="Prompt for generation")


class ONNXInput(BaseModel):
    input_text: str = Field(..., description="Text input for ONNX model")
    # Add other ONNX specific fields...


# ============= Main Request Model with Dynamic Validation =============

class InferenceRequest(BaseModel):
    """
    Unified inference request model supporting multiple AI service backends.
    """
    model_config = ConfigDict(
        extra="forbid",
        json_schema_extra={
            "examples": [
                {
                    "tool": "ollama",
                    "endpoint": "chat",
                    "session_id": "session-abc123",
                    "input": {
                        "model": "llama2",
                        "messages": [
                            {"role": "user", "content": "What is machine learning?"}
                        ]
                    }
                },
                # ... existing examples ...
            ]
        }
    )
    
    tool: ToolType = Field(..., description="Select the AI service tool to use")
    endpoint: str = Field(..., description="Endpoint to use for the selected tool")
    session_id: Optional[str] = Field(None, description="Session ID (Required for Ollama)")
    deployment_id: Optional[str] = Field(None, description="Deployment ID — optional, provide base_url in input when omitted")
    agent_id: Optional[str] = Field(None, description="Agent ID — optional, null when not provided or empty")
    onnx_model: Optional[str] = Field(None, description="ONNX model name (Required for ONNX)")
    cluster: Optional[str] = Field(None, description="Cluster type, e.g., 'kubeflow' (Optional for ONNX)")
    input: Dict[str, Any] = Field(..., description="Input payload for the inference request")

    @field_validator('endpoint')
    @classmethod
    def validate_endpoint(cls, v: str, info) -> str:
        """Validate that endpoint is valid for the selected tool"""
        data = info.data
        if 'tool' not in data:
            return v
            
        tool = data['tool']
        
        valid_endpoints = {
            ToolType.OLLAMA: ["generate", "chat", "embeddings"],
            ToolType.VLLM: ["chat_completions", "completions", "embeddings"],
            ToolType.LORAX: ["generate_root", "generate", "generate_stream", "chat_completions", "completions"],
            ToolType.ONNX: ["infer", ""],
            ToolType.ARCTIC: ["chat_completions", "completions"],
            ToolType.ANTHROPIC: ["chat_completions"],
            ToolType.GEMINI: ["chat_completions", "embeddings"],
            ToolType.OPENAI: ["chat_completions", "completions", "embeddings"],
        }
        
        allowed = valid_endpoints.get(tool)
        if allowed is not None and v not in allowed:
            raise ValueError(
                f"Invalid endpoint '{v}' for tool '{tool}'. "
                f"Valid endpoints: {', '.join(allowed)}"
            )
        
        return v

    @field_validator('deployment_id', 'agent_id', mode='before')
    @classmethod
    def empty_string_to_none(cls, v: Optional[str]) -> Optional[str]:
        """Convert empty string to None so both fields are always null when not meaningful."""
        if v == "":
            return None
        return v

    @field_validator('onnx_model')
    @classmethod
    def validate_onnx_model(cls, v: Optional[str], info) -> Optional[str]:
        """Validate onnx_model is provided for ONNX"""
        data = info.data
        if 'tool' in data and data['tool'] == ToolType.ONNX:
            if not v:
                raise ValueError("onnx_model is required for ONNX")
        return v