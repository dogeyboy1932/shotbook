import os
import re
from typing import AsyncIterator

import httpx

from .schema import SynthesizeRequest

SGLANG_TTS_URL = os.getenv("SGLANG_TTS_URL", "http://localhost:30001")

TONE_MAP = {
    "grimly": "serious",
    "whispered": "soft",
    "shouting": "loud",
    "ominously": "dramatic",
    "softly": "gentle",
    "frantically": "anxious",
}

_TONE_PATTERN = re.compile(r"\[[\w\s]+\]")


def _extract_tone_and_clean(text: str) -> tuple[str, str | None]:
    markers = _TONE_PATTERN.findall(text)
    clean = _TONE_PATTERN.sub("", text).strip()
    style = None
    if markers:
        raw = markers[0][1:-1].strip().lower()
        style = TONE_MAP.get(raw)
    return clean, style


async def synthesize_stream(
    req: SynthesizeRequest, client: httpx.AsyncClient
) -> AsyncIterator[bytes]:
    clean_text, style = _extract_tone_and_clean(req.dialogue)

    payload: dict = {
        "model": "fishaudio/fish-speech-1.5",
        "input": clean_text,
        "voice": req.speaker_id,
        "response_format": "pcm",
        "stream": True,
    }
    if style:
        payload["style"] = style

    async with client.stream(
        "POST",
        f"{SGLANG_TTS_URL}/v1/audio/speech",
        json=payload,
        timeout=None,
    ) as response:
        response.raise_for_status()
        async for chunk in response.aiter_bytes():
            if chunk:
                yield chunk
