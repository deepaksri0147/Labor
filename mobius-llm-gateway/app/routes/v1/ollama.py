from fastapi import APIRouter, HTTPException, Security, Request, Body
from fastapi.responses import StreamingResponse
from fastapi.security import HTTPBearer, HTTPAuthorizationCredentials
import logging

from app.schemas.ollama import (
    GenerateRequest,
    ChatRequest,
    EmbeddingsRequest, EmbeddingsResponse,
    ModelListResponse, RunningModelsResponse,
)
from app.services.inference.ollama_service import OllamaService
from app.utils.utils import decode_jwt, extract_identity
from app.utils.decorators import action_log
from app.schemas.action_log import ActionType, NodeType
from mobius_error import ApiException
from app.core.errors import GatewayErrors
from app.celery_app import celery_app as celery

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/ollama", tags=["Ollama"])
auth_scheme = HTTPBearer()

def get_ollama_service(request: Request) -> OllamaService:
    return OllamaService(client=request.app.state.http_client)

@router.post("/generate")
@action_log(action_type=ActionType.EXECUTE, node_type=NodeType.INFERENCE)
async def generate_response(
    request: Request,
    payload: GenerateRequest = Body(..., openapi_examples={
        "tinyllama_no_stream": {
            "summary": "TinyLlama - Generate (no stream)",
            "value": {
                "model": "tinyllama",
                "prompt": "What is machine learning?",
                "stream": False,
                "deployment_id": "<deployment-id>"
            }
        },
        "tinyllama_stream": {
            "summary": "TinyLlama - Generate (stream)",
            "value": {
                "model": "tinyllama",
                "prompt": "Explain the theory of relativity.",
                "stream": True,
                "deployment_id": "<deployment-id>"
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
    ollama_service = get_ollama_service(request)

    if payload.stream:
        await ollama_service.validate_token(token)
        request_data = {**payload.model_dump(exclude_none=True), "agent_id": payload.agent_id, "deployment_id": payload.deployment_id}
        generator = ollama_service.stream_generate(request_data=request_data, token=token, identity=identity)
        return StreamingResponse(generator, media_type="application/x-ndjson")

    task = celery.send_task(
        "ollama.generate",
        kwargs={"task_args": {
            "request_data": {**payload.model_dump(exclude_none=True), "agent_id": payload.agent_id, "deployment_id": payload.deployment_id},
            "token": token,
            "identity": identity,
        }},
        queue="ollama",
        expires=3600,
        retry=True,
        retry_policy={
            "max_retries": 3,
            "interval_start": 1,
            "interval_step": 2,
            "interval_max": 10,
        },
    )
    logger.info("Queued ollama.generate | task_id=%s", task.id)
    return {
        "status": "queued",
        "task_id": task.id,
        "message": "Inference task queued. Poll /job/{task_id} for result.",
    }

@router.post("/chat")
@action_log(action_type=ActionType.EXECUTE, node_type=NodeType.INFERENCE)
async def chat_completion(
    request: Request,
    payload: ChatRequest = Body(..., openapi_examples={
        "tinyllama_chat_no_stream": {
            "summary": "TinyLlama - Chat (no stream)",
            "value": {
                "model": "tinyllama",
                "messages": [{"role": "user", "content": "What is AI?"}],
                "stream": False,
                "deployment_id": "<deployment-id>"
            }
        },
        "tinyllama_chat_stream": {
            "summary": "TinyLlama - Chat (stream)",
            "value": {
                "model": "tinyllama",
                "messages": [
                    {"role": "system", "content": "You are a helpful assistant."},
                    {"role": "user", "content": "Tell me about Python."}
                ],
                "stream": True,
                "deployment_id": "<deployment-id>"
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
    ollama_service = get_ollama_service(request)

    if payload.stream:
        await ollama_service.validate_token(token)
        request_data = {**payload.model_dump(exclude_none=True), "agent_id": payload.agent_id, "deployment_id": payload.deployment_id}
        generator = ollama_service.stream_chat(request_data=request_data, token=token, identity=identity)
        return StreamingResponse(generator, media_type="application/x-ndjson")

    task = celery.send_task(
        "ollama.chat",
        kwargs={"task_args": {
            "request_data": {**payload.model_dump(exclude_none=True), "agent_id": payload.agent_id, "deployment_id": payload.deployment_id},
            "token": token,
            "identity": identity,
        }},
        queue="ollama",
        expires=3600,
        retry=True,
        retry_policy={
            "max_retries": 3,
            "interval_start": 1,
            "interval_step": 2,
            "interval_max": 10,
        },
    )
    logger.info("Queued ollama.chat | task_id=%s", task.id)
    return {
        "status": "queued",
        "task_id": task.id,
        "message": "Inference task queued. Poll /job/{task_id} for result.",
    }


@router.post("/embeddings", response_model=EmbeddingsResponse)
@action_log(action_type=ActionType.EXECUTE, node_type=NodeType.INFERENCE)
async def generate_embeddings(
    request: Request,
    payload: EmbeddingsRequest = Body(..., openapi_examples={
        "basic": {
            "summary": "Generate embeddings",
            "value": {
                "model": "nomic-embed-text",
                "input": "The quick brown fox jumps over the lazy dog",
                "deployment_id": "<deployment-id>",
                "session_id": "session-001"
            }
        }
    }),
    credentials: HTTPAuthorizationCredentials = Security(auth_scheme)
) -> EmbeddingsResponse:
    if not credentials or not credentials.credentials:
        raise HTTPException(status_code=401, detail="Authorization token is required")

    token = credentials.credentials
    decoded_payload = decode_jwt(token)
    
    if not decoded_payload:
        raise HTTPException(status_code=401, detail="Invalid or corrupted token")
    
    identity = extract_identity(decoded_payload)
    ollama_service = get_ollama_service(request)
    
    result = await ollama_service.embeddings(
        request_data={**payload.model_dump(exclude_none=True), "agent_id": payload.agent_id, "deployment_id": payload.deployment_id},
        token=token,
        identity=identity
    )

    return EmbeddingsResponse(**result)


@router.get("/models", response_model=ModelListResponse)
@action_log(action_type=ActionType.READ, node_type=NodeType.INFERENCE)
async def list_models(
    request: Request,
    credentials: HTTPAuthorizationCredentials = Security(auth_scheme)
) -> ModelListResponse:
    if not credentials or not credentials.credentials:
        raise HTTPException(status_code=401, detail="Authorization token is required")

    token = credentials.credentials
    ollama_service = get_ollama_service(request)
    result = await ollama_service.list_models(token)
    
    return ModelListResponse(**result)


@router.get("/models/running", response_model=RunningModelsResponse)
@action_log(action_type=ActionType.READ, node_type=NodeType.INFERENCE)
async def list_running_models(
    request: Request,
    credentials: HTTPAuthorizationCredentials = Security(auth_scheme)
) -> RunningModelsResponse:
    if not credentials or not credentials.credentials:
        raise HTTPException(status_code=401, detail="Authorization token is required")

    token = credentials.credentials
    ollama_service = get_ollama_service(request)
    result = await ollama_service.list_running_models(token)
    
    return RunningModelsResponse(**result)

