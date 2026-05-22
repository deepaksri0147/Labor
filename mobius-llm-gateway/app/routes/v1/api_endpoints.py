"""
API endpoint handlers for the LLM Gateway
"""

import time
import json
import logging
from datetime import datetime
from fastapi import HTTPException
from typing import List

from app.schemas.models import ChatCompletionRequest, ChatCompletionResponse, ModelInfo, ModelsResponse
from app.utils.formatters import InputFormatter, OutputFormatter
from app.clients.ollama_client import ollama_generate
from app.core.config import Config

# Configure logging
logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s - %(name)s - %(levelname)s - %(message)s'
)
logger = logging.getLogger(__name__)


async def create_chat_completion(request: ChatCompletionRequest) -> ChatCompletionResponse:
    """
    Create a chat completion using OpenAI compatible format
    
    Args:
        request: The chat completion request
        
    Returns:
        ChatCompletionResponse: The formatted response
        
    Raises:
        HTTPException: If the completion generation fails
    """
    # Log the input request
    logger.info("=== CHAT COMPLETION REQUEST ===")
    logger.info(f"INPUT RECEIVED: {json.dumps(request.dict(), indent=2, default=str)}")
    
    try:
        # Format input for the model
        formatted_request = InputFormatter.format_for_ollama(request)
        logger.info(f"FORMATTED REQUEST FOR OLLAMA: {json.dumps(formatted_request, indent=2, default=str)}")
        
        # Call Ollama generate function
        ollama_response = ollama_generate(
            model=formatted_request["model"],
            prompt=formatted_request["prompt"],
            stream=formatted_request["stream"],
            tools=formatted_request["tools"],
            tool_choice=formatted_request["tool_choice"],
            options=formatted_request.get("options")
        )
        # logger.info(f"OLLAMA RESPONSE: {json.dumps(ollama_response, indent=2, default=str)}")
        
        # Format output to OpenAI format
        openai_response = OutputFormatter.format_ollama_response(ollama_response, request)
        
        # Log the output response
        logger.info(f"OUTPUT GENERATED: {json.dumps(openai_response.dict(), indent=2, default=str)}")
        logger.info("=== CHAT COMPLETION COMPLETED ===")
        
        return openai_response
        
    except Exception as e:
        error_msg = f"Error generating completion: {str(e)}"
        logger.error(f"ERROR: {error_msg}")
        logger.info("=== CHAT COMPLETION FAILED ===")
        raise HTTPException(status_code=500, detail=error_msg)


async def list_models() -> ModelsResponse:
    """
    List available models
    
    Returns:
        ModelsResponse: List of available models
    """
    # Log the request
    logger.info("=== LIST MODELS REQUEST ===")
    logger.info("INPUT RECEIVED: Request to list available models")
    
    models = []
    
    for model_id in Config.get_available_models():
        model_info = ModelInfo(
            id=model_id,
            created=int(time.time()),
        )
        models.append(model_info)
    
    response = ModelsResponse(data=models)
    
    # Log the output response
    logger.info(f"OUTPUT GENERATED: {json.dumps(response.dict(), indent=2, default=str)}")
    logger.info("=== LIST MODELS COMPLETED ===")
    
    return response


async def health_check() -> dict:
    """
    Health check endpoint
    
    Returns:
        dict: Health status information
    """
    # Log the request
    logger.info("=== HEALTH CHECK REQUEST ===")
    logger.info("INPUT RECEIVED: Health check request")
    
    response = {
        "status": "healthy",
        "timestamp": datetime.now().isoformat(),
        "version": Config.API_VERSION
    }
    
    # Log the output response
    logger.info(f"OUTPUT GENERATED: {json.dumps(response, indent=2, default=str)}")
    logger.info("=== HEALTH CHECK COMPLETED ===")
    
    return response


async def root_info() -> dict:
    """
    Root endpoint with API information
    
    Returns:
        dict: API information and available endpoints
    """
    # Log the request
    logger.info("=== ROOT INFO REQUEST ===")
    logger.info("INPUT RECEIVED: Root endpoint information request")
    
    response = {
        "message": f"{Config.API_TITLE} - OpenAI Compatible API",
        "version": Config.API_VERSION,
        "description": Config.API_DESCRIPTION,
        "endpoints": {
            "chat_completions": "/v1/chat/completions",
            "models": "/v1/models",
            "health": "/health",
            "docs": "/docs",
            "redoc": "/redoc"
        }
    }
    
    # Log the output response
    logger.info(f"OUTPUT GENERATED: {json.dumps(response, indent=2, default=str)}")
    logger.info("=== ROOT INFO COMPLETED ===")
    
    return response
