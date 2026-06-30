"""Async structured-extraction client backed by the Claude API.

Ingestion used to fan chunks across a fleet of local vLLM replicas; we now use
the Anthropic API instead (the single H100 is busy serving the renderer, and
Claude is higher-quality at this structured world-state extraction). The public
surface is unchanged -- `extract_structured(...)` returns a validated instance of
the requested pydantic schema -- so `ingestion/orchestrator.py` only swapped the
class it instantiates.

Concurrency is bounded by one `asyncio.Semaphore` so the book's chunks fan out
in parallel without tripping API rate limits.
"""
from __future__ import annotations

import asyncio
import logging
from typing import TypeVar

import anthropic
from pydantic import BaseModel, ValidationError

from app.config import settings

logger = logging.getLogger("ingestion.llm_client")

SchemaT = TypeVar("SchemaT", bound=BaseModel)


class LLMExtractionError(RuntimeError):
    """Raised when a Claude call could not be coerced into the target schema
    after all retries are exhausted."""


class LLMExtractionPool:
    """Round-robins structured-JSON extractions across bounded concurrent Claude
    requests. (Name/interface kept compatible with the old GpuWorkerPool.)"""

    def __init__(self) -> None:
        if not settings.anthropic_api_key:
            raise ValueError(
                "ANTHROPIC_API_KEY is not configured -- export it (or set it in .env) before ingesting"
            )
        self._client = anthropic.AsyncAnthropic(api_key=settings.anthropic_api_key)
        self._model = settings.claude_ingest_model
        self._semaphore = asyncio.Semaphore(settings.ingest_concurrency)

    async def aclose(self) -> None:
        await self._client.close()

    async def extract_structured(
        self,
        *,
        system_prompt: str,
        user_prompt: str,
        response_schema: type[SchemaT],
        max_tokens: int = 2048,
        temperature: float = 0.1,
    ) -> SchemaT:
        """Issue a structured-output completion constrained to `response_schema`
        and return a validated instance. Retries with exponential backoff on
        transient API errors and schema-validation failures, up to
        `settings.ingest_max_retries`; raises `LLMExtractionError` on final
        failure so the orchestrator can skip the chunk."""
        last_error: Exception | None = None
        for attempt in range(1, settings.ingest_max_retries + 1):
            try:
                async with self._semaphore:
                    response = await self._client.messages.parse(
                        model=self._model,
                        max_tokens=max_tokens,
                        temperature=temperature,
                        system=system_prompt,
                        messages=[{"role": "user", "content": user_prompt}],
                        output_format=response_schema,
                    )
                return response.parsed_output
            except (anthropic.APIError, ValidationError) as exc:
                last_error = exc
                backoff_s = min(2**attempt, 30)
                logger.warning(
                    "Claude extraction attempt %d/%d failed (%s): %s -- retrying in %.1fs",
                    attempt, settings.ingest_max_retries, type(exc).__name__, exc, backoff_s,
                )
                if attempt < settings.ingest_max_retries:
                    await asyncio.sleep(backoff_s)

        raise LLMExtractionError(
            f"Exhausted {settings.ingest_max_retries} Claude attempts"
        ) from last_error


# Backwards-compatible alias so existing imports keep working.
GpuWorkerPool = LLMExtractionPool
