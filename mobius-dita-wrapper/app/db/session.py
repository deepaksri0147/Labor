"""Async SQLAlchemy engine and session factory."""
from __future__ import annotations

import logging
from collections.abc import AsyncIterator
from urllib.parse import urlparse, urlunparse

from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine

from app.core.config import get_settings

log = logging.getLogger(__name__)

_settings = get_settings()

# Use the asyncpg-compatible URL form; psycopg3 async also works with +psycopg.
engine = create_async_engine(
    _settings.database_url,
    pool_size=_settings.db_pool_size,
    max_overflow=_settings.db_max_overflow,
    pool_pre_ping=True,
    future=True,
)

SessionLocal = async_sessionmaker(engine, expire_on_commit=False, class_=AsyncSession)


async def ensure_database_exists() -> None:
    """Create the target database if it does not already exist.

    Connects to the default ``postgres`` database (which always exists) to
    check ``pg_database`` and issues ``CREATE DATABASE`` when necessary.
    """
    parsed = urlparse(_settings.database_url)
    db_name = parsed.path.lstrip("/")  # e.g. "dita_wrapper_service"
    if not db_name:
        log.warning("No database name found in DATABASE_URL; skipping auto-create.")
        return

    # Connect to the default 'postgres' database instead of the target one
    admin_url = urlunparse(parsed._replace(path="/postgres"))
    admin_engine = create_async_engine(admin_url, isolation_level="AUTOCOMMIT")

    try:
        async with admin_engine.connect() as conn:
            result = await conn.execute(
                text("SELECT 1 FROM pg_database WHERE datname = :db"),
                {"db": db_name},
            )
            if result.scalar():
                log.info("Database '%s' already exists.", db_name)
            else:
                await conn.execute(text(f'CREATE DATABASE "{db_name}"'))
                log.info("Database '%s' created successfully.", db_name)
    except Exception:
        log.warning(
            "Could not verify/create database '%s'. If the database already "
            "exists this is safe to ignore (e.g. DNS not reachable outside K8s). "
            "The application will fail later if the database is truly missing.",
            db_name,
            exc_info=True,
        )
    finally:
        await admin_engine.dispose()


async def get_session() -> AsyncIterator[AsyncSession]:
    """FastAPI dependency: yields a session, commits on success, rolls back on error."""
    async with SessionLocal() as session:
        try:
            yield session
            await session.commit()
        except Exception:
            await session.rollback()
            raise
