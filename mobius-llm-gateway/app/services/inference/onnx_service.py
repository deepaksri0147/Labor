import uuid
import logging
from datetime import datetime
from fastapi import HTTPException
import httpx
from typing import Dict, Any, Optional

from app.clients.pi_client import PIClient
from app.utils.utils import get_base_url, get_day_month, get_current_timestamp
from app.core.settings import settings

logger = logging.getLogger(__name__)

PI_SCHEMA_ID_DEPLOYMENT_ONNX = settings.PI_SCHEMA_ID_DEPLOYMENT_ONNX
KUBEFLOW_INFER_BASE_URL = settings.KUBEFLOW_INFER_BASE_URL


class ONNXService:
    def __init__(self, client: Optional[httpx.AsyncClient] = None):
        self.client = client or httpx.AsyncClient(timeout=180.0)
        self.pi_client = PIClient(settings.PI_SCHEMA_ID_DEPLOYMENT_ONNX, client=self.client)

    async def infer(
        self,
        tool_config: Dict[str, Any],
        user_input: Dict[str, Any],
        token: str,
        identity: Dict[str, str],
        tool: str
    ) -> Dict[str, Any]:
        """Execute ONNX inference"""
        deployment_id = tool_config.get("deployment_id")
        model = tool_config.get("onnx_model")
        cluster = tool_config.get("cluster")
        endpoint = tool_config.get("endpoint", "infer")
        
        if not deployment_id:
            raise HTTPException(
                status_code=400,
                detail="deployment_id is required in tool_config for ONNX"
            )
        
        if not model:
            raise HTTPException(
                status_code=400,
                detail="onnx_model is required in tool_config for ONNX"
            )
        
        # Get deployment details
        deployment_data = await self.pi_client.get_deployment_detail(token, deployment_id)
        if not deployment_data:
            raise HTTPException(
                status_code=404,
                detail=f"Deployment not found: {deployment_id}"
            )

        deployment = get_base_url(deployment_data)
        service_name = deployment_data.get("serviceName", "unknown")
        
        # Prioritize base_url from input for local testing/overrides
        base_url = user_input.get("base_url") or deployment.get("base_url")
        
        if not base_url:
            raise HTTPException(status_code=400, detail="base_url could not be determined for ONNX inference")

        # Construct URL based on cluster type
        # if cluster == "kubeflow":
        #     url = f"{KUBEFLOW_INFER_BASE_URL}/deployments/{deployment_id}/infer?endpoint={endpoint}&onnx_model={model}"
        # else:
        #     # Construct URL - handle empty endpoint for flexible server versions
        #     path = f"v2/models/{model}/{endpoint}".rstrip("/")
        #     url = f"{base_url.rstrip('/')}/{path}"

        path = f"v2/models/{model}/{endpoint}".rstrip("/")
        url = f"{base_url.rstrip('/')}/{path}"

        # Set headers
        headers = None
        # if cluster == "kubeflow":
        #     headers = {
        #         "Authorization": f"Bearer {token}",
        #         "Content-Type": "application/json",
        #         "Accept": "application/json",
        #     }

        # Execute inference
        start = datetime.utcnow()
        logger.info(f"🚀 Calling ONNX server: {url}")
        
        try:
            response = await self.client.post(url, json=user_input, timeout=180.0)
            response.raise_for_status()
            result = response.json()
        except httpx.TimeoutException:
            logger.error(f"ONNX Timeout for {url}")
            raise HTTPException(
                status_code=504,
                detail={"error": "ONNX_TIMEOUT", "message": f"Request to {url} timed out"}
            )
        except httpx.HTTPStatusError as e:
            error_detail = {"error": "ONNX_ERROR", "message": str(e)}
            try:
                error_detail["onnx_error"] = e.response.json()
            except:
                error_detail["onnx_error"] = e.response.text
            
            raise HTTPException(
                status_code=e.response.status_code,
                detail=error_detail
            )
        except Exception as e:
            logger.error(f"ONNX Unreachable: {e}")
            raise HTTPException(
                status_code=502,
                detail={"error": "ONNX_UNREACHABLE", "message": str(e), "url": url}
            )

        duration_ms = int((datetime.utcnow() - start).total_seconds() * 1000)

        # Parse response
        try:
            result = response.json()
        except ValueError:
            logger.error(f"ONNX returned non-JSON response: {response.text}")
            raise HTTPException(
                status_code=502,
                detail={"error": "ONNX_INVALID_RESPONSE"}
            )

        # Log inference
        await self._log_inference(
            tool=tool,
            deployment_id=deployment_id,
            service_name=service_name,
            request_payload=user_input,
            response_data=result,
            duration_ms=duration_ms,
            identity=identity,
            token=token,
            agent_id=deployment_id,
            model=model
        )

        return result

    async def _log_inference(
        self,
        tool: str,
        deployment_id: str,
        service_name: str,
        request_payload: Dict[str, Any],
        response_data: Dict[str, Any],
        duration_ms: int,
        identity: Dict[str, str],
        token: str,
        agent_id: str,
        model: str
    ):
        """Log inference operation to PI schema"""
        current_day, current_month = get_day_month()
        
        pi_payload = {
            "tool": tool,
            "inferid": str(uuid.uuid4()),
            "event_timestamp": get_current_timestamp(),
            "day": current_day,
            "month": current_month,
            "tenantid": identity.get("tenantId", "unknown").lower(),
            "userid": identity.get("userId", "unknown").lower(),
            "deployment_id": deployment_id,
            "agent_id": agent_id,
            "model": model,
            "request": request_payload,
            "response": response_data,
            "duration_ms": duration_ms,
            "service_name": service_name,
        }

        try:
            await self.pi_client.save_inference_instance(pi_payload, token)
            logger.info(f"✓ Inference logged for deployment: {deployment_id}")
        except Exception as e:
            logger.error(f"Failed to log inference: {e}")