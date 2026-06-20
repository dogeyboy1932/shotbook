import json
import os
import uuid

import httpx

from .prompts import SYSTEM_PROMPT, build_user_message
from .schema import HighlightRequest, ScriptBlock

_SGLANG_URL = os.getenv("SGLANG_DIRECTOR_URL", "http://localhost:30000")
_MODEL = "meta-llama/Meta-Llama-3-8B-Instruct"

_client: httpx.AsyncClient | None = None


def get_client() -> httpx.AsyncClient:
    if _client is None:
        raise RuntimeError("httpx client not initialised — call set_client() first")
    return _client


def set_client(client: httpx.AsyncClient) -> None:
    global _client
    _client = client


def close_client() -> None:
    global _client
    _client = None


async def generate_script(request: HighlightRequest) -> ScriptBlock:
    user_message = build_user_message(
        request.highlight, request.context_chunks, request.speaker_hint
    )

    payload = {
        "model": _MODEL,
        "messages": [
            {"role": "system", "content": SYSTEM_PROMPT},
            {"role": "user", "content": user_message},
        ],
        "response_format": {"type": "json_object"},
        "temperature": 0.4,
        "max_tokens": 512,
    }

    response = await get_client().post(
        f"{_SGLANG_URL}/v1/chat/completions",
        json=payload,
        timeout=60.0,
    )
    response.raise_for_status()

    raw_json = response.json()["choices"][0]["message"]["content"]

    try:
        data = json.loads(raw_json)
    except json.JSONDecodeError as exc:
        raise ValueError(f"Model returned invalid JSON: {exc}") from exc

    data["sequence_id"] = f"clip_{request.book_id}_{uuid.uuid4().hex[:8]}"

    try:
        return ScriptBlock.model_validate(data)
    except Exception as exc:
        raise ValueError(f"Model output does not match ScriptBlock schema: {exc}") from exc
