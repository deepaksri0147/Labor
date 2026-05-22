from pydantic import BaseModel, Field
from typing import Dict, Any
from datetime import datetime

class FeeBreakdown(BaseModel):
    paas_fixed_fee: float = Field(..., description="Fixed fee for PaaS usage")
    saas_fixed_fee: float = Field(..., description="Fixed fee for SaaS usage")
    api_base_fee: float = Field(..., description="Base fee for API calls")
    per_api_fee: float = Field(..., description="Fee per individual API call")

class CostSummary(BaseModel):
    input_tokens_cost: float = Field(..., description="Calculated cost for input tokens")
    output_tokens_cost: float = Field(..., description="Calculated cost for output tokens")
    total_tokens_cost: float = Field(..., description="Calculated total cost for tokens")

class CostResponse(BaseModel):
    agent_id: str = Field(..., description="Unique identifier of the agent")
    tenant_id: str = Field(..., description="Unique identifier of the tenant")
    rate_card_id: str = Field(..., description="ID of the associated rate card")
    rate_card_name: str = Field(..., description="Name of the associated rate card")
    currency: str = Field(..., description="Currency used for costs")
    fixed_fee: float = Field(..., description="Total fixed fee applied")
    fee_breakdown: FeeBreakdown
    inference_count: int = Field(..., description="Number of inference calls made")
    total_input_tokens: float = Field(..., description="Total input tokens consumed")
    total_output_tokens: float = Field(..., description="Total output tokens consumed")
    total_tokens: float = Field(..., description="Total tokens consumed (input + output)")
    cost: CostSummary
    event_timestamp: str = Field(..., description="Timestamp of the cost calculation")

    model_config = {
        "json_schema_extra": {
            "example": {
                "agent_id": "agent-12345",
                "tenant_id": "tenant-67890",
                "rate_card_id": "rc-abc-def",
                "rate_card_name": "Standard Inference Tier",
                "currency": "USD",
                "fixed_fee": 0.05,
                "fee_breakdown": {
                    "paas_fixed_fee": 0.02,
                    "saas_fixed_fee": 0.01,
                    "api_base_fee": 0.01,
                    "per_api_fee": 0.01
                },
                "inference_count": 150,
                "total_input_tokens": 12500.0,
                "total_output_tokens": 8500.0,
                "total_tokens": 21000.0,
                "cost": {
                    "input_tokens_cost": 0.25,
                    "output_tokens_cost": 0.17,
                    "total_tokens_cost": 0.42
                },
                "event_timestamp": "2024-05-04 12:34:56"
            }
        }
    }
