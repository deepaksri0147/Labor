from pydantic import BaseModel, Field
from typing import Dict, Any, Optional

class LoadTestRequest(BaseModel):
    target_url: str = Field(..., description="The full URL to test, e.g., http://localhost:8000/v1/inference/chat")
    payload: Dict[str, Any] = Field(..., description="The JSON payload to send in the POST request")
    duration_sec: int = Field(30, description="Duration of the load test in seconds")
    target_rps: int = Field(5, description="Target requests per second")
    scenario: str = Field("custom_api", description="Name of the scenario for reporting")
    test_id: Optional[str] = Field(None, description="Optional custom test run ID")
