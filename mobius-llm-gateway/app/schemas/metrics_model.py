from datetime import datetime
from typing import List, Optional, Dict, Any
from pydantic import BaseModel, Field, field_validator, ConfigDict


class BaseSchema(BaseModel):
    model_config = ConfigDict(extra="forbid")


class MetricsRequest(BaseSchema):
    start_time: Optional[str] = Field(None, description="Start time in ISO 8601 format (e.g., 2026-01-05T12:00:00Z)")
    end_time: Optional[str] = Field(None, description="End time in ISO 8601 format (e.g., 2026-01-05T12:02:00Z)")
    toolList: Optional[List[str]] = Field(None, description="List of tools to filter (e.g., ['ollama', 'lorax'])")

    @field_validator('start_time', 'end_time')
    @classmethod
    def validate_datetime_format(cls, v):
        """Validate that datetime is in correct ISO 8601 format"""
        if v is None:
            return v
        
        try:
            # Try parsing with Z suffix (UTC)
            if v.endswith('Z'):
                datetime.strptime(v, '%Y-%m-%dT%H:%M:%SZ')
            else:
                # Try parsing with timezone offset
                datetime.fromisoformat(v.replace('Z', '+00:00'))
            return v
        except ValueError:
            raise ValueError(
                f"Invalid datetime format: '{v}'. "
                "Please use ISO 8601 format: YYYY-MM-DDTHH:MM:SSZ (e.g., 2026-01-05T12:00:00Z)"
            )

    @field_validator('end_time')
    @classmethod
    def validate_time_range(cls, v, info):
        """Validate time range and dependencies"""
        data = info.data
        if v is not None and data.get('start_time') is None:
            raise ValueError(
                "start_time is required when end_time is provided. "
                "Please provide both start_time and end_time in format: YYYY-MM-DDTHH:MM:SSZ"
            )
        
        # Validate that start_time is before end_time
        if v is not None and data.get('start_time') is not None:
            try:
                start = datetime.fromisoformat(data['start_time'].replace('Z', '+00:00'))
                end = datetime.fromisoformat(v.replace('Z', '+00:00'))
                
                if start >= end:
                    raise ValueError(
                        "start_time must be before end_time. "
                        f"Provided: start_time={data['start_time']}, end_time={v}"
                    )
            except (ValueError, TypeError):
                # Let validate_datetime_format handle parsing errors
                pass
        
        return v


class OllamaModelMetrics(BaseSchema):
    model: str
    total_hits: int
    total_tokens: int
    total_input_tokens: int
    total_output_tokens: int
    avg_latency_ms: float
    min_latency_ms: float
    max_latency_ms: float
    throughput_tokens_per_sec: float


class OllamaMetrics(BaseSchema):
    total_hits: int
    total_tokens: int
    total_input_tokens: int
    total_output_tokens: int
    avg_latency_ms: float
    throughput_tokens_per_sec: float
    models: List[OllamaModelMetrics]


class LoraxServiceMetrics(BaseSchema):
    service_name: str
    total_hits: int
    total_tokens: int
    total_input_tokens: int
    total_output_tokens: int
    avg_latency_ms: float
    min_latency_ms: float
    max_latency_ms: float


class LoraxMetrics(BaseSchema):
    total_hits: int
    total_tokens: int
    total_input_tokens: int
    total_output_tokens: int
    avg_latency_ms: float
    services: List[LoraxServiceMetrics]


class VLLMServiceMetrics(BaseSchema):
    service_name: str
    total_hits: int
    total_tokens: int
    avg_latency_ms: float
    throughput_tokens_per_sec: float


class VLLMMetrics(BaseSchema):
    total_hits: int
    total_tokens: int
    avg_latency_ms: float
    throughput_tokens_per_sec: float
    services: List[VLLMServiceMetrics]


class ONNXModelMetrics(BaseSchema):
    model:  Optional[str] = None
    total_hits: int
    avg_latency_ms: float
    min_latency_ms: float
    max_latency_ms: float


class ONNXMetrics(BaseSchema):
    total_hits: int
    avg_latency_ms: float
    models: List[ONNXModelMetrics]


class MetricsResponse(BaseSchema):
    tenantId: str
    userId: str
    timeRange: Dict[str, Any]
    ollama: Optional[OllamaMetrics] = None
    lorax: Optional[LoraxMetrics] = None
    vllm: Optional[VLLMMetrics] = None
    onnx: Optional[ONNXMetrics] = None