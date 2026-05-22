import uuid
import logging
from datetime import datetime
from typing import Dict, Any, Optional

from openai import AsyncOpenAI
from fastapi import HTTPException

from app.clients.pi_client import PIClient
from app.clients.vault_client import get_llm_api_key
from app.utils.utils import get_day_month, get_current_timestamp
from app.core.settings import settings

logger = logging.getLogger(__name__)

PI_SCHEMA_ID_DEPLOYMENT = settings.PI_SCHEMA_ID_DEPLOYMENT

_PROVIDER_CONFIG = {
    "anthropic": {
        "base_url": "https://api.anthropic.com/v1/",
        "vault_key": "anthropic",
    },
    "gemini": {
        "base_url": "https://generativelanguage.googleapis.com/v1beta/openai/",
        "vault_key": "gemini",
    },
    "openai": {
        "base_url": None,
        "vault_key": "openai",
    },
}


class CloudLLMService:
    def __init__(self, provider: str):
        if provider not in _PROVIDER_CONFIG:
            raise ValueError(f"Unsupported provider: {provider}. Must be one of {list(_PROVIDER_CONFIG)}")

        config = _PROVIDER_CONFIG[provider]
        client_kwargs = {"api_key": get_llm_api_key(config["vault_key"])}
        if config["base_url"]:
            client_kwargs["base_url"] = config["base_url"]

        self.provider = provider
        self.client = AsyncOpenAI(**client_kwargs)
        self.pi_client = PIClient(PI_SCHEMA_ID_DEPLOYMENT)

    async def _log_inference(
        self,
        endpoint: str,
        model: str,
        request_payload: Dict[str, Any],
        response_data: Dict[str, Any],
        duration_ms: int,
        identity: Dict[str, str],
        token: str,
        agent_id: Optional[str] = None,
    ):
        usage = response_data.get("usage") or {}
        current_day, current_month = get_day_month()
        pi_payload = {
            "tool": f"{self.provider}_{endpoint}",
            "inferid": str(uuid.uuid4()),
            "event_timestamp": get_current_timestamp(),
            "day": current_day,
            "month": current_month,
            "tenantid": identity.get("tenantId", "unknown").lower(),
            "userid": identity.get("userId", "unknown").lower(),
            "deployment_id": None,
            "agent_id": agent_id,
            "model": model,
            "input_tokens": usage.get("prompt_tokens", 0),
            "output_tokens": usage.get("completion_tokens", 0),
            "tokens": usage.get("total_tokens", 0),
            "request": request_payload,
            "response": response_data,
            "duration_ms": duration_ms,
            "service_name": self.provider,
        }
        try:
            await self.pi_client.save_inference_instance(pi_payload, token)
        except Exception as e:
            logger.error(f"Failed to log inference: {e}")

    async def chat_completions(self, request_data: Dict[str, Any], identity: Dict[str, str], token: str) -> Dict[str, Any]:
        agent_id = request_data.pop("agent_id", None)
        request_data.pop("deployment_id", None)
        model = request_data.pop("model")
        messages = request_data.pop("messages")

        # Provider-specific params not supported by OpenAI SDK — pass via extra_body
        extra_body = {}
        for param in ("thinking",):
            if param in request_data:
                extra_body[param] = request_data.pop(param)

        try:
            start = datetime.utcnow()
            response = await self.client.chat.completions.create(
                model=model,
                messages=messages,
                extra_body=extra_body or None,
                **request_data,
            )
            duration_ms = int((datetime.utcnow() - start).total_seconds() * 1000)
        except Exception as e:
            logger.error(f"{self.provider} chat_completions error: {e}")
            raise HTTPException(status_code=502, detail={"error": f"{self.provider.upper()}_ERROR", "message": str(e)})

        result = response.model_dump()
        await self._log_inference(
            endpoint="chat",
            model=model,
            request_payload={"model": model, "messages": messages, **request_data},
            response_data=result,
            duration_ms=duration_ms,
            identity=identity,
            token=token,
            agent_id=agent_id,
        )
        return result

    async def completions(self, request_data: Dict[str, Any], identity: Dict[str, str], token: str) -> Dict[str, Any]:
        agent_id = request_data.pop("agent_id", None)
        request_data.pop("deployment_id", None)
        model = request_data.pop("model")
        prompt = request_data.pop("prompt")

        try:
            start = datetime.utcnow()
            response = await self.client.completions.create(
                model=model,
                prompt=prompt,
                **request_data,
            )
            duration_ms = int((datetime.utcnow() - start).total_seconds() * 1000)
        except Exception as e:
            logger.error(f"{self.provider} completions error: {e}")
            raise HTTPException(status_code=502, detail={"error": f"{self.provider.upper()}_ERROR", "message": str(e)})

        result = response.model_dump()
        await self._log_inference(
            endpoint="completion",
            model=model,
            request_payload={"model": model, "prompt": prompt, **request_data},
            response_data=result,
            duration_ms=duration_ms,
            identity=identity,
            token=token,
            agent_id=agent_id,
        )
        return result

    async def embeddings(self, request_data: Dict[str, Any], identity: Dict[str, str], token: str) -> Dict[str, Any]:
        agent_id = request_data.pop("agent_id", None)
        request_data.pop("deployment_id", None)
        model = request_data.pop("model")
        input_text = request_data.pop("input")

        try:
            start = datetime.utcnow()
            response = await self.client.embeddings.create(
                model=model,
                input=input_text,
                **request_data,
            )
            duration_ms = int((datetime.utcnow() - start).total_seconds() * 1000)
        except Exception as e:
            logger.error(f"{self.provider} embeddings error: {e}")
            raise HTTPException(status_code=502, detail={"error": f"{self.provider.upper()}_ERROR", "message": str(e)})

        result = response.model_dump()
        await self._log_inference(
            endpoint="embedding",
            model=model,
            request_payload={"model": model, "input": input_text, **request_data},
            response_data=result,
            duration_ms=duration_ms,
            identity=identity,
            token=token,
            agent_id=agent_id,
        )
        return result
