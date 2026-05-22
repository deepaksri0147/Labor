import httpx
import uuid
import logging
from datetime import datetime
from fastapi import HTTPException
from typing import Dict, Any, Optional

from app.clients.pi_client import PIClient
from app.utils.utils import get_base_url, get_day_month, get_current_timestamp
from app.core.settings import settings

logger = logging.getLogger(__name__)

PI_SCHEMA_ID_DEPLOYMENT = settings.PI_SCHEMA_ID_DEPLOYMENT

class VLLMService:
    def __init__(self, client: Optional[httpx.AsyncClient] = None):
        self.client = client or httpx.AsyncClient(timeout=180.0, trust_env=False)
        self.pi_client = PIClient(PI_SCHEMA_ID_DEPLOYMENT, client=self.client)

    async def _get_deployment_info(self, deployment_id: str, token: str) -> Dict[str, Any]:
        deployment_data = await self.pi_client.get_deployment_detail(token, deployment_id)
        if not deployment_data:
            raise HTTPException(status_code=404, detail=f"Deployment not found: {deployment_id}")
        deployment = get_base_url(deployment_data)
        return {
            "base_url": deployment["base_url"],
            "service_name": deployment_data.get("serviceName", "unknown"),
            "deployment_data": deployment_data
        }

    async def _resolve_base_url(self, request_data: Dict[str, Any], token: str) -> Dict[str, Any]:
        """Resolve base_url either from deployment_id (PI-Entity) or direct base_url in request."""
        deployment_id = request_data.pop("deployment_id", None)
        if deployment_id:
            info = await self._get_deployment_info(deployment_id, token)
            return {"base_url": info["base_url"], "service_name": info["service_name"], "deployment_id": deployment_id}

        base_url = request_data.pop("base_url", None)
        if not base_url:
            raise HTTPException(
                status_code=400,
                detail="Either 'deployment_id' or 'base_url' must be provided in the request"
            )
        return {"base_url": base_url.rstrip("/"), "service_name": "direct", "deployment_id": None}

    async def _make_request(self, url: str, method: str = "POST", payload: Dict[str, Any] = None, timeout: int = 60) -> Dict[str, Any]:
        """Make HTTP request to vLLM service with enhanced error handling"""
        try:
            if method == "POST":
                response = await self.client.post(url, json=payload, timeout=timeout)
            elif method == "GET":
                response = await self.client.get(url, timeout=timeout)
            else:
                raise ValueError(f"Unsupported HTTP method: {method}")

            response.raise_for_status()
            if not response.content or len(response.content) == 0:
                return {"status": "ok"}
            return response.json()

        except httpx.TimeoutException:
            raise HTTPException(status_code=504, detail={"error": "VLLM_TIMEOUT", "url": url})
        except httpx.HTTPStatusError as e:
            raise HTTPException(status_code=502, detail={"error": "VLLM_HTTP_ERROR", "url": url, "status_code": e.response.status_code, "message": e.response.text[:500]})
        except Exception as e:
            logger.error(f"vLLM request error: {e}")
            raise HTTPException(status_code=502, detail={"error": "VLLM_UNREACHABLE", "url": url, "message": str(e)})

    async def _log_inference(
        self,
        tool: str,
        deployment_id: str,
        model: str,
        service_name: str,
        request_payload: Dict[str, Any],
        response_data: Dict[str, Any],
        duration_ms: int,
        identity: Dict[str, str],
        token: str,
        agent_id: Optional[str] = None,
    ):
        """Log inference operation to PI schema"""
        usage = response_data.get("usage", {})
        current_day, current_month = get_day_month()
        pi_payload = {
            "tool": "vllm",
            "inferid": str(uuid.uuid4()),
            "event_timestamp": get_current_timestamp(),
            "day": current_day,
            "month": current_month,
            "tenantid": identity.get("tenantId", "unknown").lower(),
            "userid": identity.get("userId", "unknown").lower(),
            "deployment_id": deployment_id,
            "agent_id": agent_id,
            "model": model,
            "input_tokens": usage.get("prompt_tokens", 0),
            "output_tokens": usage.get("completion_tokens", 0),
            "tokens": usage.get("total_tokens", 0),
            "request": request_payload,
            "response": response_data,
            "duration_ms": duration_ms,
            "service_name": service_name,
        }
        try:
            await self.pi_client.save_inference_instance(pi_payload, token)
        except Exception as e:
            details = getattr(e, "details", None)
            logger.error(f"Failed to log inference: {e} | details: {details}")

    async def health_check(self, deployment_id: str, token: str) -> Dict[str, Any]:
        """Check health of vLLM deployment"""
        deployment_info = await self._get_deployment_info(deployment_id, token)
        url = f"{deployment_info['base_url']}/health"
        result = await self._make_request(url, method="GET", timeout=10)
        return {"status": result.get("status", "healthy"), "deployment_id": deployment_id, "service_name": deployment_info["service_name"]}

    async def completions(self, request_data: Dict[str, Any], identity: Dict[str, str], token: str) -> Dict[str, Any]:
        agent_id = request_data.pop("agent_id", None)
        info = await self._resolve_base_url(request_data, token)
        url = f"{info['base_url']}/v1/completions"
        start = datetime.utcnow()
        result = await self._make_request(url, payload=request_data, timeout=180)
        duration_ms = int((datetime.utcnow() - start).total_seconds() * 1000)
        await self._log_inference(
            tool="vllm_completion",
            deployment_id=info["deployment_id"],
            model=request_data.get("model"),
            service_name=info["service_name"],
            request_payload=request_data,
            response_data=result,
            duration_ms=duration_ms,
            identity=identity,
            token=token,
            agent_id=agent_id,
        )
        return result

    async def chat_completions(self, request_data: Dict[str, Any], identity: Dict[str, str], token: str) -> Dict[str, Any]:
        agent_id = request_data.pop("agent_id", None)
        info = await self._resolve_base_url(request_data, token)
        url = f"{info['base_url']}/v1/chat/completions"
        start = datetime.utcnow()
        result = await self._make_request(url, payload=request_data, timeout=180)
        duration_ms = int((datetime.utcnow() - start).total_seconds() * 1000)
        await self._log_inference(
            tool="vllm_chat",
            deployment_id=info["deployment_id"],
            model=request_data.get("model"),
            service_name=info["service_name"],
            request_payload=request_data,
            response_data=result,
            duration_ms=duration_ms,
            identity=identity,
            token=token,
            agent_id=agent_id,
        )
        return result

    async def embeddings(self, request_data: Dict[str, Any], identity: Dict[str, str], token: str) -> Dict[str, Any]:
        agent_id = request_data.pop("agent_id", None)
        info = await self._resolve_base_url(request_data, token)
        url = f"{info['base_url']}/v1/embeddings"
        start = datetime.utcnow()
        result = await self._make_request(url, payload=request_data, timeout=120)
        duration_ms = int((datetime.utcnow() - start).total_seconds() * 1000)
        await self._log_inference(
            tool="vllm_embedding",
            deployment_id=info["deployment_id"],
            model=request_data.get("model"),
            service_name=info["service_name"],
            request_payload=request_data,
            response_data=result,
            duration_ms=duration_ms,
            identity=identity,
            token=token,
            agent_id=agent_id,
        )
        return result

    async def tokenize(self, request_data: Dict[str, Any], token: str) -> Dict[str, Any]:
        info = await self._resolve_base_url(request_data, token)
        url = f"{info['base_url']}/tokenize"
        result = await self._make_request(url, payload=request_data)
        return {"tokens": result.get("tokens", []), "count": result.get("count", len(result.get("tokens", []))), "model": request_data.get("model")}

    async def detokenize(self, request_data: Dict[str, Any], token: str) -> Dict[str, Any]:
        info = await self._resolve_base_url(request_data, token)
        url = f"{info['base_url']}/detokenize"
        result = await self._make_request(url, payload=request_data)
        return {"prompt": result.get("prompt", ""), "model": request_data.get("model")}

    async def list_models(self, deployment_id: str, token: str) -> Dict[str, Any]:
        """List available models"""
        deployment_info = await self._get_deployment_info(deployment_id, token)
        url = f"{deployment_info['base_url']}/v1/models"
        result = await self._make_request(url, method="GET")
        return result

    async def get_version(self, deployment_id: str, token: str) -> Dict[str, Any]:
        """Get vLLM version"""
        deployment_info = await self._get_deployment_info(deployment_id, token)
        url = f"{deployment_info['base_url']}/version"
        result = await self._make_request(url, method="GET")
        return {"version": result.get("version", "unknown"), "deployment_id": deployment_id, "service_name": deployment_info["service_name"]}
