import httpx
import uuid
import logging
import json
import asyncio
from datetime import datetime
from fastapi import HTTPException
from typing import Dict, Any, Optional

from app.clients.pi_client import PIClient
from app.utils.utils import get_base_url, get_current_timestamp
from app.core.settings import settings

logger = logging.getLogger(__name__)

PI_SCHEMA_ID_DEPLOYMENT = settings.PI_SCHEMA_ID_DEPLOYMENT

class LoRAXService:
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

    async def _make_request(
        self,
        url: str,
        method: str = "POST",
        payload: Dict[str, Any] = None,
        timeout: int = 60,
        stream: bool = False
    ) -> Any:
        try:
            if method == "POST":
                if stream:
                    # Return the async generator for streaming
                    return await self.client.post(url, json=payload, timeout=timeout)
                response = await self.client.post(url, json=payload, timeout=timeout)
            elif method == "GET":
                response = await self.client.get(url, timeout=timeout)
            else:
                raise ValueError(f"Unsupported HTTP method: {method}")

            if not stream:
                response.raise_for_status()
                content_type = response.headers.get('content-type', '')
                if 'application/json' not in content_type:
                    return response.text
                return response.json()
            return response

        except httpx.TimeoutException:
            raise HTTPException(status_code=504, detail={"error": "LORAX_TIMEOUT", "message": f"Request to LoRAX timed out after {timeout}s"})
        except httpx.HTTPStatusError as e:
            try:
                error_detail = e.response.json()
            except:
                error_detail = e.response.text
            raise HTTPException(status_code=e.response.status_code, detail={"error": "LORAX_HTTP_ERROR", "message": str(e), "lorax_error": error_detail})
        except Exception as e:
            logger.error(f"LoRAX request error: {e}")
            raise HTTPException(status_code=502, detail={"error": "LORAX_UNREACHABLE", "message": str(e)})

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
        usage = response_data.get("usage", {})
        details = response_data.get("details", {})
        input_tokens = usage.get("prompt_tokens") or len(details.get("prefill", []))
        output_tokens = usage.get("completion_tokens") or details.get("generated_tokens", 0)
        total_tokens = usage.get("total_tokens") or (input_tokens + output_tokens)

        now = datetime.utcnow()
        current_day = now.strftime("%Y-%m-%d")
        current_month = now.strftime("%Y-%m")

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

    async def generate_root(self, request_data: Dict[str, Any], identity: Dict[str, str], token: str) -> Dict[str, Any]:
        agent_id = request_data.pop("agent_id", None)
        info = await self._resolve_base_url(request_data, token)
        is_streaming = request_data.get("stream", False)
        start = datetime.utcnow()
        result = await self._make_request(info["base_url"], payload=request_data, timeout=180, stream=is_streaming)

        if is_streaming:
            return {"stream": result}

        if isinstance(result, list): result = result[0]
        duration_ms = int((datetime.utcnow() - start).total_seconds() * 1000)
        await self._log_inference(
            tool="lorax_generate_root",
            deployment_id=info["deployment_id"],
            model=request_data.get("model") or request_data.get("parameters", {}).get("adapter_id", "unknown"),
            service_name=info["service_name"],
            request_payload=request_data,
            response_data=result,
            duration_ms=duration_ms,
            identity=identity,
            token=token,
            agent_id=agent_id,
        )
        return result

    async def generate(self, request_data: Dict[str, Any], identity: Dict[str, str], token: str) -> Dict[str, Any]:
        agent_id = request_data.pop("agent_id", None)
        info = await self._resolve_base_url(request_data, token)
        url = f"{info['base_url']}/generate"
        start = datetime.utcnow()
        result = await self._make_request(url, payload=request_data, timeout=180)
        if isinstance(result, list): result = result[0]
        duration_ms = int((datetime.utcnow() - start).total_seconds() * 1000)
        await self._log_inference(
            tool="lorax",
            deployment_id=info["deployment_id"],
            model=request_data.get("model") or request_data.get("parameters", {}).get("adapter_id", "unknown"),
            service_name=info["service_name"],
            request_payload=request_data,
            response_data=result,
            duration_ms=duration_ms,
            identity=identity,
            token=token,
            agent_id=agent_id,
        )
        return result

    async def generate_stream(self, request_data: Dict[str, Any], identity: Dict[str, str], token: str) -> Any:
        info = await self._resolve_base_url(request_data, token)
        url = f"{info['base_url']}/generate_stream"
        return await self._make_request(url, payload=request_data, timeout=180, stream=True)

    async def chat_completions(self, request_data: Dict[str, Any], identity: Dict[str, str], token: str) -> Dict[str, Any]:
        agent_id = request_data.pop("agent_id", None)
        info = await self._resolve_base_url(request_data, token)
        url = f"{info['base_url']}/v1/chat/completions"
        start = datetime.utcnow()
        result = await self._make_request(url, payload=request_data, timeout=180)
        duration_ms = int((datetime.utcnow() - start).total_seconds() * 1000)
        await self._log_inference(
            tool="lorax_chat",
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

    async def completions(self, request_data: Dict[str, Any], identity: Dict[str, str], token: str) -> Dict[str, Any]:
        agent_id = request_data.pop("agent_id", None)
        info = await self._resolve_base_url(request_data, token)
        url = f"{info['base_url']}/v1/completions"
        start = datetime.utcnow()
        result = await self._make_request(url, payload=request_data, timeout=180)
        duration_ms = int((datetime.utcnow() - start).total_seconds() * 1000)
        await self._log_inference(
            tool="lorax_completion",
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

    async def health_check(self, deployment_id: str, token: str) -> Dict[str, Any]:
        deployment_info = await self._get_deployment_info(deployment_id, token)
        url = f"{deployment_info['base_url']}/health"
        result = await self._make_request(url, method="GET", timeout=10)
        return {"status": result if isinstance(result, str) else "healthy", "deployment_id": deployment_id, "service_name": deployment_info["service_name"]}

    async def get_info(self, deployment_id: str, token: str) -> Dict[str, Any]:
        deployment_info = await self._get_deployment_info(deployment_id, token)
        url = f"{deployment_info['base_url']}/info"
        result = await self._make_request(url, method="GET", timeout=10)
        result["deployment_id"] = deployment_id
        result["service_name"] = deployment_info["service_name"]
        return result

    async def get_metrics(self, deployment_id: str, token: str) -> Dict[str, Any]:
        deployment_info = await self._get_deployment_info(deployment_id, token)
        url = f"{deployment_info['base_url']}/metrics"
        result = await self._make_request(url, method="GET", timeout=10)
        return {"metrics": result, "deployment_id": deployment_id, "service_name": deployment_info["service_name"]}
