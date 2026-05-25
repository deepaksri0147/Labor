"""FastAPI application entrypoint for the DITA service."""
from __future__ import annotations

import logging
from contextlib import asynccontextmanager
from collections.abc import AsyncIterator

from fastapi import FastAPI

from app.api import camunda, chains, document_jobs, documents, seed_packets, templates
from app.core.config import get_settings

log = logging.getLogger(__name__)
_settings = get_settings()


@asynccontextmanager
async def lifespan(app: FastAPI) -> AsyncIterator[None]:
    """Startup: ensure the database exists before the app begins serving."""
    from app.db.session import ensure_database_exists
    await ensure_database_exists()
    log.info("DITA service startup complete.")
    yield


app = FastAPI(
    title="DITA Service",
    version="1.0.0",
    description="Programmable-document substrate: templates, prompt chains, async document "
                "jobs, document lifecycle, and seed packets, over the existing render engine.",
    lifespan=lifespan,
)

app.include_router(templates.router)
app.include_router(chains.router)
app.include_router(document_jobs.router)
app.include_router(documents.router)
app.include_router(seed_packets.router)
app.include_router(camunda.router)


@app.get("/healthz", tags=["ops"])
async def healthz() -> dict:
    return {"status": "ok", "service": _settings.service_name}


@app.get("/readyz", tags=["ops"])
async def readyz() -> dict:
    return {"status": "ready"}
