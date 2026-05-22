from celery import Celery
from app.core.settings import settings

celery_app = Celery(
    "llm_gateway",
    broker=settings.RABBITMQ_URL,
    backend="rpc://",
    include=[
        "app.tasks.ollama_tasks",
        "app.tasks.vllm_tasks",
        "app.tasks.lorax_tasks",
        "app.tasks.inference_tasks",
        "app.tasks.batch_polling_task",
    ],
)

celery_app.conf.update(
    broker_connection_retry_on_startup=True,
    task_serializer="json",
    result_serializer="json",
    accept_content=["json"],
    task_track_started=True,
    task_acks_late=True,          # re-queue if worker dies mid-task
    worker_prefetch_multiplier=1, # one task at a time per worker slot
    result_expires=3600,          # results live in Redis for 1 hour
    task_routes={
        "ollama.*":     {"queue": "ollama"},
        "vllm.*":       {"queue": "vllm"},
        "lorax.*":      {"queue": "lorax"},
        "inference.*":  {"queue": "inference"},
        "batch.*":      {"queue": "batch"},
    },
)
