"""Thin async client around a fleet of OpenAI-compatible vLLM servers.

Data parallelism strategy
--------------------------
Rather than running one tensor-parallel vLLM server across all 8 H100s (which
would parallelize a *single* request across GPUs), we run 8 independent
single-GPU vLLM replicas -- one per `app.config.settings.vllm_endpoints`
entry -- and fan independent chunks of the book out across them. This is the
correct topology for an embarrassingly-parallel batch job like book
ingestion: throughput scales ~linearly with GPU count instead of paying
tensor-parallel communication overhead for no benefit.

`GpuWorkerPool` owns one `asyncio.Semaphore` per replica (bounding in-flight
requests per GPU to avoid vLLM queueing/OOM) and hands out replicas
round-robin. Callers never see which replica served a request.
"""
from __future__ import annotations

import asyncio
import itertools
import json
import logging
from typing import TypeVar

import httpx
from pydantic import BaseModel, ValidationError

from app.config import settings

logger = logging.getLogger("ingestion.llm_client")

SchemaT = TypeVar("SchemaT", bound=BaseModel)


class LLMExtractionError(RuntimeError):
    """Raised when an LLM call could not be coerced into the target schema
    after all retries are exhausted."""


class GpuWorkerPool:
    """Round-robins structured-JSON chat completions across the GPU fleet."""

    def __init__(self) -> None:
        self._endpoints = settings.vllm_endpoints
        if not self._endpoints:
            raise ValueError("No vLLM endpoints configured")

        self._clients = [
            httpx.AsyncClient(base_url=url, timeout=settings.vllm_request_timeout_s)
            for url in self._endpoints
        ]
        self._semaphores = [
            asyncio.Semaphore(settings.concurrent_requests_per_gpu) for _ in self._endpoints
        ]
        self._round_robin = itertools.cycle(range(len(self._endpoints)))
        self._dispatch_lock = asyncio.Lock()

    async def aclose(self) -> None:
        await asyncio.gather(*(client.aclose() for client in self._clients))

    async def _next_replica_index(self) -> int:
        async with self._dispatch_lock:
            return next(self._round_robin)

    async def extract_structured(
        self,
        *,
        system_prompt: str,
        user_prompt: str,
        response_schema: type[SchemaT],
        max_tokens: int = 2048,
        temperature: float = 0.1,
    ) -> SchemaT:
        """Issue a chat completion constrained to `response_schema`'s JSON
        schema and return a validated instance of it.

        Retries (with exponential backoff) on transport errors, non-2xx
        responses, truncated output, and schema-validation failures, up to
        `settings.vllm_max_retries` attempts. On final failure raises
        `LLMExtractionError` so the orchestrator can decide whether to skip
        or abort the chunk -- this function never silently returns partial
        or invalid data.
        """
        replica_index = await self._next_replica_index()
        client = self._clients[replica_index]
        semaphore = self._semaphores[replica_index]
        json_schema = response_schema.model_json_schema()

        last_error: Exception | None = None
        for attempt in range(1, settings.vllm_max_retries + 1):
            try:
                async with semaphore:
                    response = await client.post(
                        "/chat/completions",
                        json={
                            "model": settings.vllm_model_name,
                            "messages": [
                                {"role": "system", "content": system_prompt},
                                {"role": "user", "content": user_prompt},
                            ],
                            "max_tokens": max_tokens,
                            "temperature": temperature,
                            # vLLM 0.9.x guided decoding field for constrained JSON output.
                            "guided_json": json_schema,
                        },
                    )
                response.raise_for_status()
                payload = response.json()
                finish_reason = payload["choices"][0]["finish_reason"]
                raw_content = payload["choices"][0]["message"]["content"]

                if finish_reason == "length":
                    raise LLMExtractionError(
                        f"Completion truncated at max_tokens={max_tokens}; "
                        "raise max_tokens or shrink the input chunk"
                    )

                parsed_json = json.loads(raw_content)
                return response_schema.model_validate(parsed_json)

            except (httpx.HTTPError, json.JSONDecodeError, ValidationError, LLMExtractionError, KeyError) as exc:
                last_error = exc
                backoff_s = min(2 ** attempt, 30)
                logger.warning(
                    "LLM extraction attempt %d/%d failed on replica %d (%s): %s -- retrying in %.1fs",
                    attempt,
                    settings.vllm_max_retries,
                    replica_index,
                    self._endpoints[replica_index],
                    exc,
                    backoff_s,
                )
                if attempt < settings.vllm_max_retries:
                    await asyncio.sleep(backoff_s)

        raise LLMExtractionError(
            f"Exhausted {settings.vllm_max_retries} attempts against {self._endpoints[replica_index]}"
        ) from last_error
