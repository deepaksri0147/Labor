"""
Configuration settings for the LLM Gateway
"""

import os
from typing import List


class Config:
    """Configuration class for the LLM Gateway"""
    
    # Server settings
    HOST: str = os.getenv("HOST", "0.0.0.0")
    PORT: int = int(os.getenv("PORT", "8000"))
    
    # Ollama API settings
    OLLAMA_BASE_URL: str = os.getenv("OLLAMA_BASE_URL", "http://ollama-keda.mobiusdtaas.ai")
    OLLAMA_GENERATE_ENDPOINT: str = f"{OLLAMA_BASE_URL}/api/generate"
    
    # Default models
    DEFAULT_MODELS: List[str] = [
        "hf.co/naga080898/qwen3-14b-xlam-fc-gguf",
        # Add more models as needed
    ]
    
    # API settings
    API_TITLE: str = "Mobius LLM Gateway"
    API_DESCRIPTION: str = "OpenAI Compatible LLM Gateway"
    API_VERSION: str = "1.0.0"
    
    # Request timeout settings
    REQUEST_TIMEOUT: int = int(os.getenv("REQUEST_TIMEOUT", "300"))  # 5 minutes
    
    # Logging settings
    LOG_LEVEL: str = os.getenv("LOG_LEVEL", "INFO")

    # Service identity
    SERVICE_NAME: str = os.getenv("SERVICE_NAME", "mobius-llm-gateway")

    # Tracing settings
    JAEGER_URL: str = os.getenv("JAEGER_URL", "localhost")
    JAEGER_PORT: str = os.getenv("JAEGER_PORT", "14268")


    # Database settings
    DATABASE_URL: str = os.getenv("DATABASE_URL", "postgresql+asyncpg://user:pass@localhost/db")
    DB_POOL_SIZE: int = int(os.getenv("DB_POOL_SIZE", "5"))
    DB_MAX_OVERFLOW: int = int(os.getenv("DB_MAX_OVERFLOW", "10"))
    DB_POOL_RECYCLE: int = int(os.getenv("DB_POOL_RECYCLE", "3600"))
    DB_POOL_TIMEOUT: int = int(os.getenv("DB_POOL_TIMEOUT", "30"))
    
    @classmethod
    def get_available_models(cls) -> List[str]:
        """Get list of available models"""
        return cls.DEFAULT_MODELS
