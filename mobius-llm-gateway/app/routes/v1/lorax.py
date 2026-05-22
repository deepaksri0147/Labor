from fastapi import APIRouter, HTTPException, Security, Query, Request, Body
from fastapi.security import HTTPBearer, HTTPAuthorizationCredentials
from fastapi.responses import StreamingResponse
import logging

from app.schemas.lorax import (
    GenerateRequest,
    GenerateStreamRequest,
    ChatCompletionRequest,
    CompletionRequest,
    HealthResponse,
)
from app.services.inference.lorax_service import LoRAXService
from app.utils.utils import decode_jwt, extract_identity
from app.utils.decorators import action_log
from app.schemas.action_log import ActionType, ActionSource, NodeType
from mobius_error import ApiException
from app.core.errors import GatewayErrors
from app.celery_app import celery_app as celery

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/lorax", tags=["LoRAX"])
auth_scheme = HTTPBearer()


# ============= Generate =============
@router.post("/generate")
@action_log(action_type=ActionType.EXECUTE, node_type=NodeType.INFERENCE)
async def generate(
    request: Request,
    generate_request: GenerateRequest = Body(..., openapi_examples={
        "llama_generate": {
            "summary": "Llama 3.3 70B - Generate",
            "value": {
                "model": "meta-llama/Llama-3.3-70B-Instruct",
                "inputs": "What is the future of AI?",
                "deployment_id": "<deployment-id>",
                "parameters": {"max_new_tokens": 128, "temperature": 0.7}
            }
        },
        "mixtral_generate": {
            "summary": "Mixtral 8x22B - Generate",
            "value": {
                "model": "mistralai/Mixtral-8x22B-Instruct-v0.1",
                "inputs": "Explain quantum entanglement simply.",
                "deployment_id": "<deployment-id>",
                "parameters": {"max_new_tokens": 200, "temperature": 0.6}
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
    tenant_id = identity.get("tenantId")
    user_id = identity.get("userId")
    
    if not tenant_id:
        raise HTTPException(status_code=401, detail="tenantId not found in token")
    
    if not user_id:
        raise HTTPException(status_code=401, detail="userId not found in token")

    task = celery.send_task(
        "lorax.generate",
        kwargs={"task_args": {
            "request_data": {**generate_request.model_dump(exclude_none=True), "agent_id": generate_request.agent_id},
            "token": token,
            "identity": identity,
        }},
        queue="lorax",
        expires=3600,
        retry=True,
        retry_policy={
            "max_retries": 3,
            "interval_start": 1,
            "interval_step": 2,
            "interval_max": 10,
        },
    )
    logger.info("Queued lorax.generate | task_id=%s", task.id)
    return {
        "status": "queued",
        "task_id": task.id,
        "message": "Inference task queued. Poll /job/{task_id} for result.",
    }


# ============= Generate Stream =============
@router.post("/generate_stream")
@action_log(action_type=ActionType.EXECUTE, node_type=NodeType.INFERENCE)
async def generate_stream(
    request: Request,
    stream_request: GenerateStreamRequest = Body(..., openapi_examples={
        "llama_stream": {
            "summary": "Llama 3.3 70B - Stream",
            "value": {
                "model": "meta-llama/Llama-3.3-70B-Instruct",
                "inputs": "Write a short story about robots.",
                "deployment_id": "<deployment-id>",
                "parameters": {"max_new_tokens": 256, "temperature": 0.8}
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
    tenant_id = identity.get("tenantId")
    user_id = identity.get("userId")
    
    if not tenant_id:
        raise HTTPException(status_code=401, detail="tenantId not found in token")
    
    if not user_id:
        raise HTTPException(status_code=401, detail="userId not found in token")

    lorax_service = LoRAXService()
    response = await lorax_service.generate_stream(
        request_data={**stream_request.model_dump(exclude_none=True), "agent_id": stream_request.agent_id},
        identity=identity,
        token=token
    )
    
    return StreamingResponse(
        response.aiter_bytes(chunk_size=8192),
        media_type="text/event-stream"
    )


# ============= Chat Completions =============
@router.post("/v1/chat/completions")
@action_log(action_type=ActionType.EXECUTE, node_type=NodeType.INFERENCE)
async def create_chat_completion(
    request: Request,
    chat_request: ChatCompletionRequest = Body(..., openapi_examples={
        "llama_chat_lora": {
            "summary": "Llama 3.3 70B - Chat (with LoRA adapter)",
            "value": {
                "model": "meta-llama/Llama-3.3-70B-Instruct",
                "messages": [
                    {"role": "system", "content": "You are a helpful assistant."},
                    {"role": "user", "content": "Explain neural networks."}
                ],
                "deployment_id": "<deployment-id>",
                "adapter_id": "my-custom-lora",
                "max_tokens": 256,
                "temperature": 0.7
            }
        },
        "mixtral_chat": {
            "summary": "Mixtral 8x22B - Chat (no adapter)",
            "value": {
                "model": "mistralai/Mixtral-8x22B-Instruct-v0.1",
                "messages": [
                    {"role": "user", "content": "What are the benefits of renewable energy?"}
                ],
                "deployment_id": "<deployment-id>",
                "max_tokens": 512,
                "temperature": 0.6
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
    tenant_id = identity.get("tenantId")
    user_id = identity.get("userId")

    if not tenant_id:
        raise HTTPException(status_code=401, detail="tenantId not found in token")

    if not user_id:
        raise HTTPException(status_code=401, detail="userId not found in token")

    task = celery.send_task(
        "lorax.chat_completions",
        kwargs={"task_args": {
            "request_data": {**chat_request.model_dump(exclude_none=True), "agent_id": chat_request.agent_id},
            "token": token,
            "identity": identity,
        }},
        queue="lorax",
        expires=3600,
        retry=True,
        retry_policy={
            "max_retries": 3,
            "interval_start": 1,
            "interval_step": 2,
            "interval_max": 10,
        },
    )
    logger.info("Queued lorax.chat_completions | task_id=%s", task.id)
    return {
        "status": "queued",
        "task_id": task.id,
        "message": "Inference task queued. Poll /job/{task_id} for result.",
    }


# ============= Completions =============
@router.post("/v1/completions")
@action_log(action_type=ActionType.EXECUTE, node_type=NodeType.INFERENCE)
async def create_completion(
    request: Request,
    completion_request: CompletionRequest = Body(..., openapi_examples={
        "llama_completion": {
            "summary": "Llama 3.3 70B - Completion",
            "value": {
                "model": "meta-llama/Llama-3.3-70B-Instruct",
                "prompt": "The future of artificial intelligence is",
                "deployment_id": "<deployment-id>",
                "max_tokens": 150,
                "temperature": 0.7
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
    tenant_id = identity.get("tenantId")
    user_id = identity.get("userId")

    if not tenant_id:
        raise HTTPException(status_code=401, detail="tenantId not found in token")

    if not user_id:
        raise HTTPException(status_code=401, detail="userId not found in token")

    task = celery.send_task(
        "lorax.completions",
        kwargs={"task_args": {
            "request_data": {**completion_request.model_dump(exclude_none=True), "agent_id": completion_request.agent_id},
            "token": token,
            "identity": identity,
        }},
        queue="lorax",
        expires=3600,
        retry=True,
        retry_policy={
            "max_retries": 3,
            "interval_start": 1,
            "interval_step": 2,
            "interval_max": 10,
        },
    )
    logger.info("Queued lorax.completions | task_id=%s", task.id)
    return {
        "status": "queued",
        "task_id": task.id,
        "message": "Inference task queued. Poll /job/{task_id} for result.",
    }


# ============= Health Check =============
@router.get("/health", response_model=HealthResponse)
@action_log(action_type=ActionType.READ, node_type=NodeType.INFERENCE)
async def health_check(
    request: Request,
    deployment_id: str = Query(..., description="Deployment ID"),
    credentials: HTTPAuthorizationCredentials = Security(auth_scheme)
) -> HealthResponse:
    
    if not credentials or not credentials.credentials:
        raise HTTPException(status_code=401, detail="Authorization token is required")

    token = credentials.credentials
    decoded_payload = decode_jwt(token)
    
    if not decoded_payload:
        raise HTTPException(status_code=401, detail="Invalid or corrupted token")
    
    identity = extract_identity(decoded_payload)
    tenant_id = identity.get("tenantId")
    user_id = identity.get("userId")
    
    if not tenant_id:
        raise HTTPException(status_code=401, detail="tenantId not found in token")
    
    if not user_id:
        raise HTTPException(status_code=401, detail="userId not found in token")

    lorax_service = LoRAXService()
    result = await lorax_service.health_check(deployment_id, token)
    
    return HealthResponse(**result)

