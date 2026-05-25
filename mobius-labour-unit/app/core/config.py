"""Application configuration, loaded from environment."""
from __future__ import annotations

from functools import lru_cache

from pydantic import Field
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_prefix="GATEWAY_", env_file=".env", extra="ignore")

    # Service
    service_name: str = "labor-gateway"
    environment: str = "local"
    log_level: str = "INFO"

    # Postgres
    database_url: str = Field(
        default="postgresql+psycopg://gateway:gateway@localhost:5432/labor_gateway"
    )
    db_pool_size: int = 10
    db_max_overflow: int = 20

    # Redis (queue + cache-prefix store + event stream)
    redis_url: str = "redis://localhost:6379/0"
    queue_name: str = "labor:jobs"
    event_stream_prefix: str = "labor:events:"  # one stream per job_id
    event_retention_seconds: int = 7 * 24 * 3600  # >= 7 days per the notification contract

    # Downstream inference (the EXISTING gateway capability we call at the leaf)
    inference_base_url: str = "http://localhost:9094"
    inference_chat_path: str = "/inference/chat"
    inference_token: str | None = None
    inference_timeout_seconds: float = 120.0

    # Data wrapper service (the external CRUD API for ingest/retrieve/update).
    # The wrapper is called with the bearer token forwarded from the inbound request;
    # there is no server-side default token.
    wrapper_base_url: str = "http://0.0.0.0:8002"
    wrapper_timeout_seconds: float = 30.0

    # Bob-Service / Bob-Camunda (workflow engine integration)
    bob_base_url: str = "http://bob-service:8080"
    bob_timeout_seconds: float = 30.0
    bob_worker_id: str = "labor-gateway-worker-1"
    # external-task topics this gateway services
    topic_labor_job: str = "labor.execute"        # coarse: whole labor job as one task
    topic_labor_validate: str = "labor.validate"  # fine-grained (optional, human-in-loop)
    topic_labor_repair: str = "labor.repair"      # fine-grained (optional, human-in-loop)
    enable_external_task_worker: bool = True
    external_task_poll_max: int = 5
    external_task_lock_ms: int = 300000

    # Auth
    jwt_issuer: str = "https://auth.internal"
    jwt_audience: str = "labor-gateway"
    jwt_jwks_url: str = "http://auth.internal/.well-known/jwks.json"
    auth_disabled: bool = False  # tests/local only; never true in prod

    # Limits
    max_batch_items: int = 5000
    default_batch_concurrency: int = 8
    idempotency_ttl_seconds: int = 7 * 24 * 3600


@lru_cache
def get_settings() -> Settings:
    return Settings()
