from typing import Optional, List, Dict, Any
from pydantic import BaseModel, Field, field_validator, ConfigDict
from datetime import datetime


class BaseSchema(BaseModel):
    model_config = ConfigDict(extra="forbid")


class TimeRange(BaseSchema):
    """Time range for filtering"""
    from_time: Optional[str] = Field(None, alias="from", description="Start time in ISO 8601 format")
    to_time: Optional[str] = Field(None, alias="to", description="End time in ISO 8601 format")
    
    @field_validator('from_time', 'to_time')
    @classmethod
    def validate_iso_format(cls, v):
        if v:
            try:
                datetime.fromisoformat(v.replace('Z', '+00:00'))
            except ValueError:
                raise ValueError(f"Invalid ISO 8601 datetime format: {v}")
        return v


class UsageFilters(BaseSchema):
    """Optional filters for usage aggregation"""
    user_id: Optional[str] = None
    model: Optional[str] = None
    session_id: Optional[str] = None


class OllamaUsageRequest(BaseSchema):
    """Request schema for Ollama usage aggregation"""
    model_config = ConfigDict(
        extra="forbid",
        populate_by_name=True
    )
    tenant_id: str = Field(..., description="Tenant ID (mandatory)")
    filters: Optional[UsageFilters] = Field(default_factory=UsageFilters, description="Optional filters")
    time_range: Optional[TimeRange] = Field(None, description="Optional time range filter")
    group_by: Optional[List[str]] = Field(
        default_factory=list,
        description="Dimensions to group by: user_id, model, session_id, day, month"
    )
    metrics: List[str] = Field(
        ...,
        min_length=1,
        description="Metrics to return: input_tokens, output_tokens, total_tokens, request_count"
    )
    
    @field_validator('group_by')
    @classmethod
    def validate_group_by(cls, v):
        valid_dimensions = ['user_id', 'model', 'session_id', 'day', 'month']
        if v:
            for dim in v:
                if dim not in valid_dimensions:
                    raise ValueError(
                        f"Invalid group_by dimension: '{dim}'. "
                        f"Valid options: {', '.join(valid_dimensions)}"
                    )
        return v
    
    @field_validator('metrics')
    @classmethod
    def validate_metrics(cls, v):
        valid_metrics = ['input_tokens', 'output_tokens', 'total_tokens', 'request_count']
        if not v:
            raise ValueError("At least one metric must be provided")
        
        for metric in v:
            if metric not in valid_metrics:
                raise ValueError(
                    f"Invalid metric: '{metric}'. "
                    f"Valid options: {', '.join(valid_metrics)}"
                )
        return v


class UsageGroup(BaseSchema):
    """Grouping dimensions in response"""
    user_id: Optional[str] = None
    model: Optional[str] = None
    session_id: Optional[str] = None
    day: Optional[str] = None
    month: Optional[str] = None


class UsageMetrics(BaseSchema):
    """Aggregated metrics"""
    input_tokens: Optional[int] = None
    output_tokens: Optional[int] = None
    total_tokens: Optional[int] = None
    request_count: Optional[int] = None


class UsageResult(BaseSchema):
    """Single result row with group and metrics"""
    group: UsageGroup
    metrics: UsageMetrics


class OllamaUsageResponse(BaseSchema):
    """Response schema for Ollama usage aggregation"""
    model_config = ConfigDict(
        extra="forbid",
        json_schema_extra={
            "example": {
                "tenant_id": "tenantID",
                "results": [
                    {
                        "group": {
                            "user_id": "userId",
                            "model": "gpt-oss:20b",
                            "day": "2025-12-17"
                        },
                        "metrics": {
                            "input_tokens": 1580,
                            "output_tokens": 620,
                            "total_tokens": 2200,
                            "request_count": 14
                        }
                    }
                ]
            }
        }
    )
    tenant_id: str
    results: List[Dict[str, Any]] = Field(default_factory=list)