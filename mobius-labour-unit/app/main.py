"""FastAPI application entrypoint for the Labor Gateway."""
from __future__ import annotations

from contextlib import asynccontextmanager

from fastapi import FastAPI

from app.api import batch, calls, camunda, jobs
from app.core.config import get_settings
from app.services.executors import seed_default_executors

_settings = get_settings()


@asynccontextmanager
async def lifespan(app: FastAPI):
    seed_default_executors()
    yield


app = FastAPI(
    title="Labor Gateway",
    version="1.0.0",
    description="Typed LLM-as-labor execution surface over the existing inference gateway.",
    root_path="/mobius-labour-unit",
    lifespan=lifespan,
)

app.include_router(calls.router)
app.include_router(jobs.router)
app.include_router(batch.batch_router)
app.include_router(batch.cache_router)
app.include_router(batch.exec_router)
app.include_router(camunda.router)


@app.get("/healthz", tags=["ops"])
async def healthz() -> dict:
    return {"status": "ok", "service": _settings.service_name}


@app.get("/readyz", tags=["ops"])
async def readyz() -> dict:
    # a real readiness check pings Postgres + Redis; kept light here
    return {"status": "ready"}
