from typing import Union
from fastapi import APIRouter, HTTPException, Security, Query, Request, Body
from fastapi.security import HTTPBearer, HTTPAuthorizationCredentials
import logging

from app.schemas.vllm import (
    HealthResponse,
    ModelsListResponse,
    ChatCompletionRequest,
    CompletionRequest,
    EmbeddingRequest, EmbeddingResponse,
)
from app.services.inference.vllm_service import VLLMService
from app.utils.utils import decode_jwt, extract_identity
from app.utils.decorators import action_log
from app.schemas.action_log import ActionType, ActionSource, NodeType
from mobius_error import ApiException
from app.core.errors import GatewayErrors
from app.celery_app import celery_app as celery

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/vllm", tags=["vLLM"])
auth_scheme = HTTPBearer()

vllm_service = VLLMService()

@router.get("/health", response_model=HealthResponse)
@action_log(action_type=ActionType.READ, node_type=NodeType.INFERENCE)
async def health_check_route(
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

    result = await vllm_service.health_check(deployment_id, token)
    
    return HealthResponse(**result)

@router.get("/models", response_model=ModelsListResponse)
@action_log(action_type=ActionType.READ, node_type=NodeType.INFERENCE)
async def list_models(
    request: Request,
    deployment_id: str = Query(..., description="Deployment ID"),
    credentials: HTTPAuthorizationCredentials = Security(auth_scheme)
) -> ModelsListResponse:
    
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

    result = await vllm_service.list_models(deployment_id, token)
    
    return ModelsListResponse(**result)


@router.post("/chat/completions")
@action_log(action_type=ActionType.EXECUTE, node_type=NodeType.INFERENCE)
async def create_chat_completion(
    request: Request,
    chat_request: ChatCompletionRequest = Body(..., openapi_examples={
        "llama_chat": {
            "summary": "Llama 3.1 8B - Chat",
            "value": {
                "model": "meta-llama/Meta-Llama-3.1-8B-Instruct",
                "messages": [
                    {"role": "system", "content": "You are a helpful assistant."},
                    {"role": "user", "content": "What is deep learning?"}
                ],
                "deployment_id": "<deployment-id>",
                "max_tokens": 512,
                "temperature": 0.7
            }
        },
        "qwen3_chat": {
            "summary": "Qwen3 32B - Chat",
            "value": {
                "model": "Qwen/Qwen3-32B",
                "messages": [
                    {"role": "user", "content": "Explain the transformer architecture."}
                ],
                "deployment_id": "<deployment-id>",
                "max_tokens": 512,
                "temperature": 0.6
            }
        },
        "phi4_chat": {
            "summary": "Phi-4 Mini - Chat",
            "value": {
                "model": "microsoft/phi-4-mini-instruct",
                "messages": [
                    {"role": "system", "content": "You are a concise assistant."},
                    {"role": "user", "content": "Summarise the theory of evolution."}
                ],
                "deployment_id": "<deployment-id>",
                "max_tokens": 256,
                "temperature": 0.5
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
        "vllm.chat_completions",
        kwargs={"task_args": {
            "request_data": {**chat_request.model_dump(exclude_none=True), "agent_id": chat_request.agent_id},
            "token": token,
            "identity": identity,
        }},
        queue="vllm",
        expires=3600,
        retry=True,
        retry_policy={
            "max_retries": 3,
            "interval_start": 1,
            "interval_step": 2,
            "interval_max": 10,
        },
    )
    logger.info("Queued vllm.chat_completions | task_id=%s", task.id)
    return {
        "status": "queued",
        "task_id": task.id,
        "message": "Inference task queued. Poll /job/{task_id} for result.",
    }


@router.post("/completions")
@action_log(action_type=ActionType.EXECUTE, node_type=NodeType.INFERENCE)
async def create_completion(
    request: Request,
    completion_request: CompletionRequest = Body(..., openapi_examples={
        "llama_completion": {
            "summary": "Llama 3.1 8B - Completion",
            "value": {
                "model": "meta-llama/Meta-Llama-3.1-8B-Instruct",
                "prompt": "The future of artificial intelligence is",
                "deployment_id": "<deployment-id>",
                "max_tokens": 128,
                "temperature": 0.8
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
        "vllm.completions",
        kwargs={"task_args": {
            "request_data": {**completion_request.model_dump(exclude_none=True), "agent_id": completion_request.agent_id},
            "token": token,
            "identity": identity,
        }},
        queue="vllm",
        expires=3600,
        retry=True,
        retry_policy={
            "max_retries": 3,
            "interval_start": 1,
            "interval_step": 2,
            "interval_max": 10,
        },
    )
    logger.info("Queued vllm.completions | task_id=%s", task.id)
    return {
        "status": "queued",
        "task_id": task.id,
        "message": "Inference task queued. Poll /job/{task_id} for result.",
    }
@router.post("/embeddings", response_model=Union[EmbeddingResponse, dict])
@action_log(action_type=ActionType.EXECUTE, node_type=NodeType.INFERENCE)
async def create_embedding(
    request: Request,
    embedding_request: EmbeddingRequest = Body(..., openapi_examples={
        "qwen3_embeddings": {
            "summary": "Qwen3-Embedding-8B",
            "value": {
                "model": "Qwen/Qwen3-Embedding-8B",
                "input": ["Machine learning is a subset of artificial intelligence.", "Deep learning uses neural networks."],
                "deployment_id": "<deployment-id>"
            }
        },
        "minilm_embeddings": {
            "summary": "all-MiniLM-L6-v2",
            "value": {
                "model": "sentence-transformers/all-MiniLM-L6-v2",
                "input": ["Text to embed", "Another text to embed"],
                "deployment_id": "<deployment-id>"
            }
        }
    }),
    credentials: HTTPAuthorizationCredentials = Security(auth_scheme)
) -> any:

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

    result = await vllm_service.embeddings(
        request_data={**embedding_request.model_dump(exclude_none=True), "agent_id": embedding_request.agent_id},
        identity=identity,
        token=token
    )

    return result