"""Ingestion runtime configuration, loaded from environment variables.

The ingestion pipeline (Claude extraction -> Supabase) is the only consumer.
The VM renderer has its own settings in services/renderer/config.py.
"""
from __future__ import annotations

from pydantic import Field
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=".env", env_prefix="BVG_", extra="ignore")

    # --- PostgreSQL (Supabase) ------------------------------------------
    database_url: str = "postgresql+asyncpg://postgres:postgres@localhost:5432/book_video_gen"
    db_pool_size: int = 20
    db_max_overflow: int = 10
    # Optional path to a CA cert PEM to verify the database's TLS (Supabase's
    # private root CA). When set, the async engine uses a fully-verified SSL
    # context. Unset = no SSL (local dev).
    db_ssl_ca: str | None = None

    # --- Claude ingestion ------------------------------------------------
    # Reads the SDK's conventional env var name (no BVG_ prefix) so a plain
    # `export ANTHROPIC_API_KEY=...` just works.
    anthropic_api_key: str | None = Field(default=None, validation_alias="ANTHROPIC_API_KEY")
    claude_ingest_model: str = "claude-opus-4-8"
    ingest_concurrency: int = 4          # max in-flight Claude extraction calls
    ingest_max_retries: int = 3
    paragraph_chunk_size: int = 4        # paragraphs grouped into one Pass-2 call


settings = Settings()
