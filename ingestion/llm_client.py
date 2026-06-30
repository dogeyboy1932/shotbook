"""Async structured-extraction client backed by the Claude API.

Ingestion used to fan chunks across a fleet of local vLLM replicas; we now use
the Anthropic API instead (the single H100 is busy serving the renderer, and
Claude is higher-quality at this structured world-state extraction). The public
surface is unchanged -- `extract_structured(...)` returns a validated instance of
the requested pydantic schema -- so `ingestion/orchestrator.py` only swapped the
class it instantiates.

We use PROMPT-BASED JSON (ask for JSON in the system prompt, prefill "{" to force
it, then json.loads + pydantic-validate) rather than the SDK's grammar-constrained
structured outputs: the ingestion schemas (deeply nested paragraph-beat extraction)
are complex enough that constrained decoding hits "Grammar compilation timed out"
400s. Prompt-based extraction has no grammar to compile and Opus is reliable at
emitting valid JSON; we retry + validate to catch the rare malformed response.

Concurrency is bounded by one `asyncio.Semaphore` so the book's chunks fan out in
parallel without tripping API rate limits.
"""
from __future__ import annotations

import asyncio
import json
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


def _strip_to_json(text: str) -> str:
    """Best-effort: pull the JSON object out of a model response (handles an
    accidental ```json fence or leading prose)."""
    text = text.strip()
    if text.startswith("```"):
        text = text.split("```", 2)[1]
        if text.startswith("json"):
            text = text[4:]
        text = text.strip()
    start = text.find("{")
    end = text.rfind("}")
    if start != -1 and end != -1 and end > start:
        return text[start : end + 1]
    return text


class LLMExtractionPool:
    """Bounded-concurrency structured-JSON extraction over the Claude API.
    (Name/interface kept compatible with the old GpuWorkerPool.)"""

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
        temperature: float = 0.1,  # accepted for back-compat; not sent (deprecated on newer models)
    ) -> SchemaT:
        _ = temperature
        schema_json = json.dumps(response_schema.model_json_schema())
        system = (
            system_prompt
            + "\n\nReturn ONLY a single JSON object that conforms to this JSON Schema. "
            "No prose, no markdown fences.\n\nJSON Schema:\n" + schema_json
        )

        last_error: Exception | None = None
        for attempt in range(1, settings.ingest_max_retries + 1):
            try:
                async with self._semaphore:
                    response = await self._client.messages.create(
                        model=self._model,
                        max_tokens=max_tokens,
                        system=system,
                        messages=[{"role": "user", "content": user_prompt}],
                    )
                if response.stop_reason == "max_tokens":
                    raise LLMExtractionError(
                        f"Completion truncated at max_tokens={max_tokens}; raise it or shrink the chunk"
                    )
                text = "".join(b.text for b in response.content if getattr(b, "type", None) == "text")
                parsed = json.loads(_strip_to_json(text))
                return response_schema.model_validate(parsed)
            except (anthropic.APIError, json.JSONDecodeError, ValidationError, LLMExtractionError) as exc:
                last_error = exc
                backoff_s = min(2**attempt, 30)
                logger.warning(
                    "Claude extraction attempt %d/%d failed (%s): %s -- retrying in %.1fs",
                    attempt, settings.ingest_max_retries, type(exc).__name__,
                    str(exc)[:200], backoff_s,
                )
                if attempt < settings.ingest_max_retries:
                    await asyncio.sleep(backoff_s)

        raise LLMExtractionError(
            f"Exhausted {settings.ingest_max_retries} Claude attempts"
        ) from last_error


# Backwards-compatible alias so existing imports keep working.
GpuWorkerPool = LLMExtractionPool
