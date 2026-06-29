"""Async SQLAlchemy engine/session management, shared by the API and the
ingestion orchestrator.
"""
from __future__ import annotations

import ssl
from collections.abc import AsyncGenerator
from contextlib import asynccontextmanager

from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine

from app.config import settings

# When a CA cert is configured (e.g. connecting to Supabase, whose pooler uses a
# private CA), build a verified SSL context and hand it to asyncpg via
# connect_args. Without it, connect plainly (local Postgres).
_connect_args: dict = {}
if settings.db_ssl_ca:
    _connect_args["ssl"] = ssl.create_default_context(cafile=settings.db_ssl_ca)

engine = create_async_engine(
    settings.database_url,
    pool_size=settings.db_pool_size,
    max_overflow=settings.db_max_overflow,
    pool_pre_ping=True,
    connect_args=_connect_args,
)

AsyncSessionFactory = async_sessionmaker(
    bind=engine,
    expire_on_commit=False,
    autoflush=False,
)


async def get_db_session() -> AsyncGenerator[AsyncSession, None]:
    """FastAPI dependency: yields one session per request, always closed."""
    async with AsyncSessionFactory() as session:
        yield session


@asynccontextmanager
async def db_session_scope() -> AsyncGenerator[AsyncSession, None]:
    """Context manager for non-request callers (the ingestion orchestrator).

    Commits on clean exit, rolls back and re-raises on any exception.
    """
    session = AsyncSessionFactory()
    try:
        yield session
        await session.commit()
    except Exception:
        await session.rollback()
        raise
    finally:
        await session.close()
