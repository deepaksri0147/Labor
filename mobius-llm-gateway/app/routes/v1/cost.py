from fastapi import APIRouter, Security, HTTPException, Request, Body
from fastapi.security import HTTPBearer, HTTPAuthorizationCredentials
from pydantic import BaseModel
from typing import Optional
import logging

from app.utils.utils import decode_jwt, extract_identity
from app.utils.decorators import action_log
from app.schemas.action_log import ActionType, NodeType
from app.services.cost_service import CostService
from app.schemas.cost import CostResponse

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/usage-cost", tags=["Cost"])
auth_scheme = HTTPBearer()


class CostRequest(BaseModel):
    agent_id: str
    tenant_id: str


@router.post("", response_model=CostResponse)
@action_log(action_type=ActionType.READ, node_type=NodeType.INFERENCE)
async def calculate_inference_cost(
    request: Request,
    body: CostRequest = Body(..., openapi_examples={
        "basic": {
            "summary": "Get usage cost for an agent",
            "description": "Returns total token usage and estimated cost aggregated across all inference calls made by this agent under the given tenant.",
            "value": {
                "agent_id": "3fa85f64-5717-4562-b3fc-2c963f66afa6",
                "tenant_id": "org-mobius-prod"
            }
        }
    }),
    credentials: HTTPAuthorizationCredentials = Security(auth_scheme)
):
    if not credentials or not credentials.credentials:
        raise HTTPException(status_code=401, detail="Authorization token is required")

    token = credentials.credentials
    decoded_payload = decode_jwt(token)
    if not decoded_payload:
        raise HTTPException(status_code=401, detail="Invalid or corrupted token")

    identity = extract_identity(decoded_payload)
    if not identity.get("tenantId"):
        raise HTTPException(status_code=401, detail="tenantId not found in token")
    if not identity.get("userId"):
        raise HTTPException(status_code=401, detail="userId not found in token")

    cost_service = CostService()
    return await cost_service.calculate_cost(
        agent_id=body.agent_id,
        tenant_id=body.tenant_id,
        token=token
    )
