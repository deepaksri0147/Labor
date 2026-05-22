# app/services/ollama_service.py
import asyncio
import httpx
import uuid
import logging
import json
from datetime import datetime, timezone
from fastapi import HTTPException
from typing import AsyncIterator, Dict, Any, Optional

from app.clients.pi_client import PIClient
from app.core.settings import settings
from app.core.config import Config
from app.utils.utils import get_current_timestamp, get_day_month

logger = logging.getLogger(__name__)

PI_SCHEMA_ID_OLLAMA_SESSION_ID = settings.PI_SCHEMA_ID_OLLAMA_SESSION_ID


class OllamaService:
    def __init__(self, client: Optional[httpx.AsyncClient] = None):
        base_url = settings.OLLAMA_API_URL.rstrip("/")
        if not base_url:
            raise RuntimeError("OLLAMA_API_URL environment variable is not set")

        self.base_url = base_url
        self.client = client or httpx.AsyncClient(timeout=Config.REQUEST_TIMEOUT)
        self.pi_client = PIClient(settings.PI_SCHEMA_ID_DEPLOYMENT, client=self.client)

    def _get_timestamp(self) -> str:
        """Get current UTC timestamp in ISO format"""
        return datetime.now(timezone.utc).isoformat()

    async def validate_token(self, token: str) -> bool:
        """Validate the bearer token by making a simple query"""
        query = f"SELECT 1 FROM t_{PI_SCHEMA_ID_OLLAMA_SESSION_ID}_t LIMIT 1"
        try:
            await self.pi_client.execute_query(query, token)
            return True
        except Exception as e:
            logger.error(f"Token validation failed: {e}")
            raise HTTPException(
                status_code=401,
                detail="Invalid or expired authorization token"
            )

    async def validate_session(self, session_id: str, token: str) -> bool:
        """Validate that session_id exists in the database"""
        query = f"""
        SELECT session_id
        FROM t_{PI_SCHEMA_ID_OLLAMA_SESSION_ID}_t
        WHERE session_id = '{session_id}'
        LIMIT 1
        """
        try:
            logger.info(f"Validating session_id: {session_id}")
            result = await self.pi_client.execute_query(query, token)

            if result and len(result) > 0:
                logger.info(f"✓ Valid session_id: {session_id}")
                return True

            logger.warning(f"✗ Invalid session_id: {session_id}")
            raise HTTPException(
                status_code=403,
                detail=f"Invalid session_id '{session_id}'"
            )

        except HTTPException:
            raise
        except Exception as e:
            logger.exception("Session validation failed")
            raise HTTPException(
                status_code=500,
                detail=f"Failed to validate session_id: {str(e)}"
            )

    async def _make_request(
        self,
        endpoint: str,
        method: str = "POST",
        payload: Dict[str, Any] = None,
        timeout: int = 180,
        stream: bool = True
    ) -> Dict[str, Any]:
        """Make HTTP request to Ollama API using async httpx"""
        url = f"{self.base_url}{endpoint}"
        
        try:
            if method == "POST":
                response = await self.client.post(
                    url,
                    json=payload,
                    timeout=timeout
                )
            elif method == "GET":
                response = await self.client.get(url, timeout=timeout)
            elif method == "DELETE":
                response = await self.client.request(method="DELETE", url=url, json=payload, timeout=timeout)
            else:
                raise ValueError(f"Unsupported HTTP method: {method}")

            response.raise_for_status()

            # For large responses or streaming, we'd handle it differently, 
            # but currently most endpoints returning JSON
            try:
                result = response.json()
            except ValueError:
                logger.error(f"Ollama returned non-JSON response: {response.text}")
                raise HTTPException(
                    status_code=502,
                    detail={
                        "error": "OLLAMA_INVALID_RESPONSE",
                        "message": "Ollama returned empty or non-JSON response",
                        "status_code": response.status_code,
                        "response": response.text,
                    }
                )

            return {
                "status_code": response.status_code,
                "result": result
            }

        except httpx.TimeoutException:
            raise HTTPException(
                status_code=504,
                detail={
                    "error": "OLLAMA_TIMEOUT",
                    "message": f"Request to Ollama timed out after {timeout}s",
                }
            )
        except httpx.HTTPStatusError as e:
            error_detail = {"error": "OLLAMA_HTTP_ERROR", "message": str(e)}
            try:
                error_detail["ollama_error"] = e.response.json()
            except:
                error_detail["ollama_error"] = e.response.text
            
            raise HTTPException(
                status_code=e.response.status_code,
                detail=error_detail
            )
        except Exception as e:
            raise HTTPException(status_code=502, detail={"error": "OLLAMA_UNREACHABLE", "message": str(e)})

    async def save_inference_log(
        self,
        tool: str,
        session_id: str,
        model: str,
        request_payload: Dict[str, Any],
        response_data: Dict[str, Any],
        duration_ms: int,
        identity: Dict[str, str],
        token: str,
        agent_id: Optional[str] = None,
        deployment_id: Optional[str] = None,
    ):
        """Save inference operation to PI schema (Background compatible)"""
        result = response_data.get("result", {})
        current_day, current_month = get_day_month()

        # Flatten and clean up the result for "neat and clean" storage
        clean_result = result
        if isinstance(clean_result, dict) and "context" in clean_result:
            # Drop the massive 'context' array to avoid polluting the schema
            clean_result = clean_result.copy()
            del clean_result["context"]

        pi_payload = {
            "tool": tool,
            "inferid": str(uuid.uuid4()),
            "event_timestamp": get_current_timestamp(),
            "day": current_day,
            "month": current_month,
            "tenantid": identity.get("tenantId", "unknown").lower(),
            "userid": identity.get("userId", "unknown").lower(),
            "session_id": session_id,
            "deployment_id": deployment_id,
            "agent_id": agent_id,
            "model": model,
            "input_tokens": result.get("prompt_eval_count", 0),
            "output_tokens": result.get("eval_count", 0),
            "tokens": (
                result.get("prompt_eval_count", 0)
                + result.get("eval_count", 0)
            ),
            "request": request_payload,
            "response": clean_result,
            "duration_ms": duration_ms,
        }

        try:
            await self.pi_client.save_inference_instance(pi_payload, token)
            logger.info(f"✓ Inference logged for session: {session_id}")
        except Exception as e:
            details = getattr(e, "details", None)
            logger.error(f"Failed to log inference: {e} | details: {details}")

    async def _stream_ndjson(
        self,
        endpoint: str,
        payload: Dict[str, Any],
        session_id: str,
        model: str,
        tool: str,
        request_payload: Dict[str, Any],
        identity: Dict[str, str],
        token: str,
        agent_id: Optional[str],
        deployment_id: Optional[str],
        start: datetime,
    ) -> AsyncIterator[bytes]:
        url = f"{self.base_url}{endpoint}"
        timeout = httpx.Timeout(connect=30.0, read=None)
        last_chunk: Dict[str, Any] = {}

        try:
            async with httpx.AsyncClient(timeout=timeout) as stream_client:
                async with stream_client.stream("POST", url, json=payload) as response:
                    response.raise_for_status()
                    async for line in response.aiter_lines():
                        if line:
                            yield line.encode("utf-8") + b"\n"
                            await asyncio.sleep(0)
                            try:
                                chunk = json.loads(line)
                                if chunk.get("done"):
                                    last_chunk = chunk
                            except Exception:
                                pass
        except httpx.HTTPStatusError as e:
            error_body: Dict[str, Any] = {"error": "OLLAMA_HTTP_ERROR", "message": str(e)}
            try:
                error_body["ollama_error"] = e.response.json()
            except Exception:
                error_body["ollama_error"] = e.response.text
            yield json.dumps(error_body).encode("utf-8") + b"\n"
            return
        except Exception as e:
            yield json.dumps({"error": "OLLAMA_UNREACHABLE", "message": str(e)}).encode("utf-8") + b"\n"
            return

        duration_ms = int((datetime.now(timezone.utc) - start).total_seconds() * 1000)
        await self.save_inference_log(
            tool=tool,
            session_id=session_id,
            model=model,
            request_payload=request_payload,
            response_data={"result": last_chunk},
            duration_ms=duration_ms,
            identity=identity,
            token=token,
            agent_id=agent_id,
            deployment_id=deployment_id,
        )

    async def stream_generate(
        self,
        request_data: Dict[str, Any],
        token: str,
        identity: Dict[str, str],
    ) -> AsyncIterator[bytes]:
        """Stream generate completion as NDJSON chunks (token already validated by caller)."""
        agent_id = request_data.pop("agent_id", None)
        deployment_id = request_data.pop("deployment_id", None)
        session_id = request_data.pop("session_id", None) or str(uuid.uuid4())
        start = datetime.now(timezone.utc)

        payload = {"model": request_data["model"], "prompt": request_data["prompt"], "stream": True}

        async for chunk in self._stream_ndjson(
            endpoint="/api/generate",
            payload=payload,
            session_id=session_id,
            model=request_data["model"],
            tool="ollama_generate",
            request_payload=request_data,
            identity=identity,
            token=token,
            agent_id=agent_id,
            deployment_id=deployment_id,
            start=start,
        ):
            yield chunk

    async def stream_chat(
        self,
        request_data: Dict[str, Any],
        token: str,
        identity: Dict[str, str],
    ) -> AsyncIterator[bytes]:
        """Stream chat completion as NDJSON chunks (token already validated by caller)."""
        agent_id = request_data.pop("agent_id", None)
        deployment_id = request_data.pop("deployment_id", None)
        session_id = request_data.pop("session_id", None) or str(uuid.uuid4())
        start = datetime.now(timezone.utc)

        payload = {"model": request_data["model"], "messages": request_data["messages"], "stream": True}

        async for chunk in self._stream_ndjson(
            endpoint="/api/chat",
            payload=payload,
            session_id=session_id,
            model=request_data["model"],
            tool="ollama_chat",
            request_payload=request_data,
            identity=identity,
            token=token,
            agent_id=agent_id,
            deployment_id=deployment_id,
            start=start,
        ):
            yield chunk

    async def generate(
        self,
        request_data: Dict[str, Any],
        token: str,
        identity: Dict[str, str]
    ) -> Dict[str, Any]:
        """Generate completion from a prompt"""
        await self.validate_token(token)
        agent_id = request_data.pop("agent_id", None)
        deployment_id = request_data.pop("deployment_id", None)
        session_id = request_data.pop("session_id", None) or str(uuid.uuid4())

        start = datetime.now(timezone.utc)
        
        payload = {
            "model": request_data["model"],
            "prompt": request_data["prompt"],
            "stream": request_data.get("stream", False),
        }

        response = await self._make_request(
            "/api/generate",
            method="POST",
            payload=payload,
        )

        duration_ms = int((datetime.now(timezone.utc) - start).total_seconds() * 1000)

        await self.save_inference_log(
            tool="ollama_generate",
            session_id=session_id,
            model=request_data["model"],
            request_payload=request_data,
            response_data=response,
            duration_ms=duration_ms,
            identity=identity,
            token=token,
            agent_id=agent_id,
            deployment_id=deployment_id,
        )

        return response

    async def chat(
        self,
        request_data: Dict[str, Any],
        token: str,
        identity: Dict[str, str]
    ) -> Dict[str, Any]:
        """Generate chat completion"""
        await self.validate_token(token)
        agent_id = request_data.pop("agent_id", None)
        deployment_id = request_data.pop("deployment_id", None)
        session_id = request_data.pop("session_id", None) or str(uuid.uuid4())

        start = datetime.now(timezone.utc)

        payload = {
            "model": request_data["model"],
            "messages": request_data["messages"],
            "stream": request_data.get("stream", False),
        }

        response = await self._make_request(
            "/api/chat",
            method="POST",
            payload=payload,
        )

        duration_ms = int((datetime.now(timezone.utc) - start).total_seconds() * 1000)

        await self.save_inference_log(
            tool="ollama_chat",
            session_id=session_id,
            model=request_data["model"],
            request_payload=request_data,
            response_data=response,
            duration_ms=duration_ms,
            identity=identity,
            token=token,
            agent_id=agent_id,
            deployment_id=deployment_id,
        )

        return response

    async def embeddings(
        self,
        request_data: Dict[str, Any],
        token: str,
        identity: Dict[str, str]
    ) -> Dict[str, Any]:
        """Generate embeddings"""
        await self.validate_token(token)
        agent_id = request_data.pop("agent_id", None)
        deployment_id = request_data.pop("deployment_id", None)
        session_id = request_data.pop("session_id", None) or str(uuid.uuid4())

        start = datetime.now(timezone.utc)

        response = await self._make_request(
            "/api/embed",
            method="POST",
            payload=request_data,
            timeout=120
        )

        duration_ms = int((datetime.now(timezone.utc) - start).total_seconds() * 1000)

        await self.save_inference_log(
            tool="ollama_embeddings",
            session_id=session_id,
            model=request_data["model"],
            request_payload=request_data,
            response_data=response,
            duration_ms=duration_ms,
            identity=identity,
            token=token,
            agent_id=agent_id,
            deployment_id=deployment_id,
        )

        return response

    async def list_models(self, token: str) -> Dict[str, Any]:
        """List available models"""
        await self.validate_token(token)
        return await self._make_request("/api/tags", method="GET")

    async def list_running_models(self, token: str) -> Dict[str, Any]:
        """List running models"""
        await self.validate_token(token)
        return await self._make_request("/api/ps", method="GET")

    async def show_model(self, model_name: str, verbose: bool, token: str) -> Dict[str, Any]:
        """Show model information"""
        await self.validate_token(token)
        payload = {"name": model_name}
        if verbose is not None:
            payload["verbose"] = verbose
        return await self._make_request("/api/show", payload=payload)

    async def create_model(self, request_data: Dict[str, Any], token: str) -> Dict[str, Any]:
        """Create a model from Modelfile"""
        await self.validate_token(token)
        payload = {
            "from": request_data["from_model"],
            "model": request_data["model"],
            "system": request_data["system"],
            "stream": False
        }
        return await self._make_request(
            "/api/create",
            payload=payload,
            stream=request_data.get("stream", False),
            timeout=600
        )

    async def copy_model(self, source: str, destination: str, token: str) -> Dict[str, Any]:
        """Copy a model"""
        await self.validate_token(token)
        payload = {"source": source, "destination": destination}
        return await self._make_request("/api/copy", payload=payload, timeout=180)

    async def delete_model(self, model_name: str, token: str) -> Dict[str, Any]:
        """Delete a model"""
        await self.validate_token(token)
        payload = {"name": model_name}
        return await self._make_request("/api/delete", method="DELETE", payload=payload, timeout=180)

    async def push_model(self, request_data: Dict[str, Any], token: str) -> Dict[str, Any]:
        """Push a model to registry"""
        await self.validate_token(token)
        return await self._make_request(
            "/api/push",
            payload=request_data,
            stream=request_data.get("stream", False),
            timeout=600
        )

    async def get_version(self, token: str) -> Dict[str, Any]:
        """Get Ollama version"""
        await self.validate_token(token)
        return await self._make_request("/api/version", method="GET")