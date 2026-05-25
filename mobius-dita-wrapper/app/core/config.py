"""DITA service configuration."""
from __future__ import annotations

from functools import lru_cache

from pydantic import Field
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_prefix="DITA_", env_file=".env", extra="ignore")

    service_name: str = "dita-service"
    environment: str = "local"
    log_level: str = "INFO"

    database_url: str = Field(
        default="postgresql+psycopg://dita:dita@localhost:5432/dita_wrapper_service"
    )
    db_pool_size: int = 10
    db_max_overflow: int = 20

    redis_url: str = "redis://localhost:6379/1"
    queue_name: str = "dita:jobs"
    chain_queue_name: str = "dita:chain-runs"
    event_stream_prefix: str = "dita:events:"
    event_retention_seconds: int = 7 * 24 * 3600

    # downstream: the EXISTING synchronous DITA render engine (Express service)
    # Mirrors the real routes mounted at API_PREFIX + /v1/* (see dita-serviceexisting/dita).
    render_base_url: str = "http://localhost:8080"
    render_api_prefix: str = "/api/v1"
    render_dita_path: str = "/dita"                # POST → resolveMap (bundle_id|chunk_ids → html|text|pdf)
    render_bob_llm_path: str = "/dita/bob-llm"     # POST → resolveBobLlm (resolve + forward to Bob)
    render_bundles_path: str = "/bundles"          # CRUD on bundles
    render_chunks_path: str = "/chunks"            # CRUD on chunks
    render_acl_path: str = "/acl"                  # grant/revoke/validate/node-relation
    render_resolved_results_path: str = "/resolved-results"  # historical resolve runs
    render_timeout_seconds: float = 60.0
    render_auth_header: str = ""                   # bearer/principal passthrough; blank = no header

    # downstream: the Labor Gateway (for call_llm chain steps + seed-packet to-llm)
    # Mirrors labor-gateway routers (see Labor/labor-gateway/app/api/*.py).
    labor_gateway_base_url: str = "http://localhost:8000"
    labor_timeout_seconds: float = 180.0
    labor_auth_header: str = ""                              # bearer passthrough; blank = no header

    # Camunda push entry points (used by chain engine, doc-jobs, seed-packets today)
    labor_run_sync_path: str = "/camunda/labor/run-sync"
    labor_run_async_path: str = "/camunda/labor/run-async"
    labor_workflows_publish_path: str = "/camunda/workflows/publish"
    labor_reconcile_path: str = "/camunda/labor/jobs"        # /{job_id}/reconcile?pipeline_id=...

    # Direct labor surface (labor/* router)
    labor_calls_path: str = "/labor/calls"                   # POST submit + GET /{labor_call_id}
                                                             # plus /{id}/validate, /repair, /revalidate
    labor_jobs_path: str = "/labor/jobs"                     # GET /{job_id} + cancel/retry/fork/pause/resume
                                                             # /artifacts /lineage /ux-state /events /emit-event
    labor_batch_path: str = "/labor/batch"                   # POST + GET /{batch_id} + /items + /cancel
    labor_cache_prefix_path: str = "/labor/cache-prefix"     # POST + GET /{cache_prefix_id}
    labor_executors_path: str = "/executors"
    labor_executor_policies_path: str = "/executor-policies"
    labor_model_policies_path: str = "/model-policies"

    # downstream: Bob-Service (ETL hydration + Camunda workflow engine)
    bob_base_url: str = "http://bob-service:8080"
    bob_timeout_seconds: float = 30.0
    bob_worker_id: str = "dita-service-worker-1"
    topic_document_job: str = "dita.document_job"   # coarse external task
    topic_chain_run: str = "dita.chain_run"
    enable_external_task_worker: bool = True
    external_task_poll_max: int = 5
    external_task_lock_ms: int = 300000

    # auth
    jwt_issuer: str = "https://auth.internal"
    jwt_audience: str = "dita-service"
    jwt_jwks_url: str = "http://auth.internal/.well-known/jwks.json"
    auth_disabled: bool = False

    max_chain_depth: int = 25  # bounded re-entry guard


@lru_cache
def get_settings() -> Settings:
    return Settings()
