from fastapi import APIRouter, HTTPException, Request
from fastapi.security import HTTPBearer, HTTPAuthorizationCredentials
from fastapi import Security, Body
import logging

from typing import List

# Initialize logger properly
logger = logging.getLogger(__name__)

router = APIRouter(prefix="/metrics", tags=["Metrics"])

auth_scheme = HTTPBearer()

from app.schemas.metrics_model import MetricsResponse, MetricsRequest
from app.utils.utils import decode_jwt, extract_identity
from app.utils.decorators import action_log
from app.schemas.action_log import ActionType, ActionSource, NodeType
from mobius_error import ApiException
from app.core.errors import GatewayErrors
from app.services.metrics.metrics_service import UnifiedMetricsService
from app.clients.pi_client import PIClient
from app.core.settings import settings

from app.services.metrics.ollama_usage_service import OllamaUsageService
from app.schemas.ollama_usage import OllamaUsageRequest, OllamaUsageResponse
from app.clients.pi_client import PIClientError

    
PI_SCHEMA_ID_DEPLOYMENT = settings.PI_SCHEMA_ID_DEPLOYMENT
    
pi_client = PIClient(PI_SCHEMA_ID_DEPLOYMENT)
metrics_service = UnifiedMetricsService(pi_client)
service = OllamaUsageService()

@router.post("/", response_model=MetricsResponse)
@action_log(action_type=ActionType.READ, node_type=NodeType.METRICS)
async def get_metrics(
    request: Request,
    credentials: HTTPAuthorizationCredentials = Security(auth_scheme),
    body: MetricsRequest = Body(...)
):
    if not credentials or not credentials.credentials:
        raise HTTPException(status_code=401, detail="Authorization token is required")

    token = credentials.credentials
    decoded_payload = decode_jwt(token)
    
    if not decoded_payload:
        raise HTTPException(status_code=401, detail="Invalid or corrupted token")
    
    identity = extract_identity(decoded_payload)
    tenant_id = identity.get("tenantId")
    user_id = identity.get("userId")
    if not tenant_id or not user_id:
        raise HTTPException(400, "Invalid token: missing tenantId or userId")
    
    return await metrics_service.get_unified_metrics(
        tenant_id=tenant_id,
        user_id=user_id,
        request=body,
        token=token
    )
    

@router.post(
    "/ollama/aggregate",
    response_model=OllamaUsageResponse,
    summary="Aggregate Ollama Token Usage",
)
@action_log(action_type=ActionType.READ, node_type=NodeType.METRICS)
async def aggregate_ollama_usage(
    request: Request,
    usage_request: OllamaUsageRequest,
    credentials: HTTPAuthorizationCredentials = Security(auth_scheme),
) -> OllamaUsageResponse:
    if not credentials or not credentials.credentials:
        raise HTTPException(status_code=401, detail="Authorization token is required")

    token = credentials.credentials
    decoded_payload = decode_jwt(token)
    try:
        logger.info(f"Received Ollama usage aggregation request for tenant: {usage_request.tenant_id}")
        logger.debug(f"Request details: filters={usage_request.filters}, group_by={usage_request.group_by}, metrics={usage_request.metrics}")

        # Call service to aggregate usage
        response = await service.aggregate_usage(request=usage_request, token=token)
        
        logger.info(f"✓ Successfully processed request, returning {len(response['results'])} result groups")
        
        return response
        
    except PIClientError as e:
        logger.error(f"PIClient error: {e.message}")
        raise HTTPException(
            status_code=500,
            detail={
                "message": e.message,
                "details": e.details
            }
        )
    
    except ValueError as e:
        logger.error(f"Validation error: {e}")
        raise HTTPException(
            status_code=400,
            detail=str(e)
        )
    
    except Exception as e:
        logger.error(f"Unexpected error in aggregate_ollama_usage: {e}")
        raise HTTPException(
            status_code=500,
            detail=f"Internal server error: {str(e)}"
        )

# --- System Metrics & Diagnostics ---

from app.services.metrics_collector import system_collector
from app.middleware.metrics_middleware import MetricsMiddleware


@router.get("/system")
@action_log(action_type=ActionType.READ, node_type=NodeType.METRICS)
async def get_system_util_metrics(
    request: Request,
    credentials: HTTPAuthorizationCredentials = Security(auth_scheme),
):
    """Real-time system utilisation metrics: CPU, memory, GPU, replica count."""
    if not credentials or not credentials.credentials:
        raise HTTPException(status_code=401, detail="Authorization token is required")
    return system_collector.get_summary()


@router.get("/flush-status")
async def get_flush_status():
    """
    Diagnostic — no auth required.
    Shows the last PI flush result per endpoint so you can confirm data is
    reaching the schema or see the exact error when it fails.
    """
    results = MetricsMiddleware.flush_results
    tracked = list(MetricsMiddleware.global_stats.keys())
    pending = [k for k in tracked if k not in results]
    return {
        "session_id":         MetricsMiddleware._session_id,
        "schema_id":          settings.PI_SCHEMA_ID_LOAD_TEST,
        "tracked_endpoints":  tracked,
        "pending_flush":      pending,
        "last_flush_results": results,
    }

