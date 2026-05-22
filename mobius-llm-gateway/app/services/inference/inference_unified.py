import logging
import uuid
from typing import Dict, Any
from fastapi import HTTPException

from app.services.inference.vllm_service import VLLMService
from app.services.inference.ollama_service import OllamaService
from app.services.inference.lorax_service import LoRAXService
from app.services.inference.onnx_service import ONNXService
from app.services.inference.cloud_llm_service import CloudLLMService

logger = logging.getLogger(__name__)


class InferenceService:
    def __init__(self):
        self.vllm_service = VLLMService()
        self.ollama_service = OllamaService()
        self.lorax_service = LoRAXService()
        self.onnx_service = ONNXService()
        self._cloud_services: dict = {}

    def _cloud(self, provider: str) -> CloudLLMService:
        if provider not in self._cloud_services:
            self._cloud_services[provider] = CloudLLMService(provider)
        return self._cloud_services[provider]

    async def execute_inference(
        self,
        tool: str,
        tool_config: Dict[str, Any],
        user_input: Dict[str, Any],
        token: str,
        identity: Dict[str, str]
    ) -> Dict[str, Any]:
        endpoint = tool_config.get("endpoint")

        if not endpoint:
            raise HTTPException(status_code=400, detail="endpoint is required in tool_config")

        try:
            if tool == "ollama":
                return await self._execute_ollama(endpoint, tool_config, user_input, token, identity)
            elif tool == "vllm":
                return await self._execute_vllm(endpoint, tool_config, user_input, token, identity)
            elif tool == "lorax":
                return await self._execute_lorax(endpoint, tool_config, user_input, token, identity)
            elif tool == "onnx":
                return await self._execute_onnx(endpoint, tool_config, user_input, token, identity)
            elif tool == "arctic":
                return await self._execute_arctic(endpoint, tool_config, user_input, token, identity)
            elif tool == "anthropic":
                return await self._execute_anthropic(endpoint, tool_config, user_input, token, identity)
            elif tool == "gemini":
                return await self._execute_gemini(endpoint, tool_config, user_input, token, identity)
            elif tool == "openai":
                return await self._execute_openai(endpoint, tool_config, user_input, token, identity)
            else:
                raise HTTPException(status_code=400, detail=f"Unsupported tool: {tool}")

        except HTTPException:
            raise
        except Exception as e:
            logger.exception(f"Inference execution failed for tool={tool}, endpoint={endpoint}")
            raise HTTPException(status_code=500, detail=f"Inference execution failed: {str(e)}")

    async def _execute_ollama(
        self,
        endpoint: str,
        tool_config: Dict[str, Any],
        user_input: Dict[str, Any],
        token: str,
        identity: Dict[str, str]
    ) -> Dict[str, Any]:
        session_id = tool_config.get("session_id") or str(uuid.uuid4())
        request_data = {
            **user_input,
            "session_id": session_id,
            "agent_id": tool_config.get("agent_id"),
            "deployment_id": tool_config.get("deployment_id"),
        }

        if endpoint == "generate":
            return await self.ollama_service.generate(request_data, token, identity)
        elif endpoint == "chat":
            return await self.ollama_service.chat(request_data, token, identity)
        elif endpoint == "embeddings":
            return await self.ollama_service.embeddings(request_data, token, identity)
        else:
            raise HTTPException(status_code=400, detail=f"Unsupported Ollama endpoint: {endpoint}")

    async def _execute_vllm(
        self,
        endpoint: str,
        tool_config: Dict[str, Any],
        user_input: Dict[str, Any],
        token: str,
        identity: Dict[str, str]
    ) -> Dict[str, Any]:
        request_data = {**user_input}
        request_data["agent_id"] = tool_config.get("agent_id")
        if tool_config.get("deployment_id"):
            request_data["deployment_id"] = tool_config["deployment_id"]

        if endpoint == "chat_completions":
            return await self.vllm_service.chat_completions(request_data, identity, token)
        elif endpoint == "completions":
            return await self.vllm_service.completions(request_data, identity, token)
        elif endpoint == "embeddings":
            return await self.vllm_service.embeddings(request_data, identity, token)
        else:
            raise HTTPException(status_code=400, detail=f"Unsupported vLLM endpoint: {endpoint}")

    async def _execute_lorax(
        self,
        endpoint: str,
        tool_config: Dict[str, Any],
        user_input: Dict[str, Any],
        token: str,
        identity: Dict[str, str]
    ) -> Dict[str, Any]:
        request_data = {**user_input}
        request_data["agent_id"] = tool_config.get("agent_id")
        if tool_config.get("deployment_id"):
            request_data["deployment_id"] = tool_config["deployment_id"]

        if endpoint == "generate_root":
            return await self.lorax_service.generate_root(request_data, identity, token)
        elif endpoint == "generate":
            return await self.lorax_service.generate(request_data, identity, token)
        elif endpoint == "generate_stream":
            return await self.lorax_service.generate_stream(request_data, identity, token)
        elif endpoint == "chat_completions":
            return await self.lorax_service.chat_completions(request_data, identity, token)
        elif endpoint == "completions":
            return await self.lorax_service.completions(request_data, identity, token)
        else:
            raise HTTPException(status_code=400, detail=f"Unsupported LoRAX endpoint: {endpoint}")

    async def _execute_arctic(
        self,
        endpoint: str,
        tool_config: Dict[str, Any],
        user_input: Dict[str, Any],
        token: str,
        identity: Dict[str, str]
    ) -> Dict[str, Any]:
        return await self._execute_vllm(endpoint, tool_config, user_input, token, identity)

    async def _execute_anthropic(
        self,
        endpoint: str,
        tool_config: Dict[str, Any],
        user_input: Dict[str, Any],
        token: str,
        identity: Dict[str, str]
    ) -> Dict[str, Any]:
        request_data = {**user_input, "agent_id": tool_config.get("agent_id")}
        svc = self._cloud("anthropic")
        if endpoint == "chat_completions":
            return await svc.chat_completions(request_data, identity, token)
        raise HTTPException(status_code=400, detail=f"Unsupported Anthropic endpoint: {endpoint}")

    async def _execute_gemini(
        self,
        endpoint: str,
        tool_config: Dict[str, Any],
        user_input: Dict[str, Any],
        token: str,
        identity: Dict[str, str]
    ) -> Dict[str, Any]:
        request_data = {**user_input, "agent_id": tool_config.get("agent_id")}
        svc = self._cloud("gemini")
        if endpoint == "chat_completions":
            return await svc.chat_completions(request_data, identity, token)
        elif endpoint == "embeddings":
            return await svc.embeddings(request_data, identity, token)
        raise HTTPException(status_code=400, detail=f"Unsupported Gemini endpoint: {endpoint}")

    async def _execute_openai(
        self,
        endpoint: str,
        tool_config: Dict[str, Any],
        user_input: Dict[str, Any],
        token: str,
        identity: Dict[str, str]
    ) -> Dict[str, Any]:
        request_data = {**user_input, "agent_id": tool_config.get("agent_id")}
        svc = self._cloud("openai")
        if endpoint == "chat_completions":
            return await svc.chat_completions(request_data, identity, token)
        elif endpoint == "completions":
            return await svc.completions(request_data, identity, token)
        elif endpoint == "embeddings":
            return await svc.embeddings(request_data, identity, token)
        raise HTTPException(status_code=400, detail=f"Unsupported OpenAI endpoint: {endpoint}")

    async def _execute_onnx(
        self,
        endpoint: str,
        tool_config: Dict[str, Any],
        user_input: Dict[str, Any],
        token: str,
        identity: Dict[str, str]
    ) -> Dict[str, Any]:
        if endpoint != "infer":
            raise HTTPException(
                status_code=400,
                detail=f"Unsupported ONNX endpoint: {endpoint}. Only 'infer' is supported."
            )

        return await self.onnx_service.infer(
            tool_config=tool_config,
            user_input=user_input,
            token=token,
            identity=identity,
            tool="onnx"
        )