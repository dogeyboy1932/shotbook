import os
import re
from typing import AsyncIterator

import httpx
import ormsgpack

from .schema import SynthesizeRequest

# Fish Speech 1.5.1 native API server (tools/api_server.py). Despite the legacy
# env var name, this is NOT an OpenAI-compatible endpoint: it serves POST /v1/tts
# with an ormsgpack-encoded ServeTTSRequest body and streams raw 16-bit PCM.
FISH_TTS_URL = os.getenv("SGLANG_TTS_URL", "http://localhost:30001")

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


def _pcm_offset(buffer: bytes | bytearray) -> int | None:
    """Locate the first PCM sample after a RIFF/WAVE header.

    Scans for the ``data`` sub-chunk marker and skips it plus its 4-byte size
    field; everything after that is raw PCM. Returns None until enough bytes
    have arrived to make the determination.
    """
    idx = buffer.find(b"data")
    if idx == -1:
        return None
    pcm_start = idx + 8  # len(b"data") + 4-byte chunk size
    if len(buffer) < pcm_start:
        return None
    return pcm_start


async def synthesize_stream(
    req: SynthesizeRequest, client: httpx.AsyncClient
) -> AsyncIterator[bytes]:
    # Fish Speech has no tone/style parameter; we still strip the [tone] marker
    # so it is never spoken aloud.
    clean_text, _ = _extract_tone_and_clean(req.dialogue)

    # speaker_id maps to a Fish Speech reference voice. An unknown id is safe:
    # the server creates an empty references/<id>/ folder and falls back to the
    # default voice. Populate that folder with a sample .wav + .lab to pin a
    # timbre per speaker.
    payload: dict = {
        "text": clean_text,
        "format": "wav",
        "streaming": True,
        "reference_id": req.speaker_id or None,
        "chunk_length": 200,
        "normalize": True,
        "use_memory_cache": "on",
    }

    body = ormsgpack.packb(payload)

    # In streaming mode Fish Speech's server filters its output to bytes-only
    # chunks, and the WAV header it emits is a numpy array (not bytes), so it
    # gets dropped: the body is pure raw s16le PCM with NO header — exactly what
    # the mixer's `-f s16le` input wants, so we pass it straight through. We
    # still guard against a leading RIFF header (e.g. a non-streaming server
    # config) and strip it if present.
    header_resolved = False
    prefix = bytearray()
    async with client.stream(
        "POST",
        f"{FISH_TTS_URL}/v1/tts",
        content=body,
        headers={"content-type": "application/msgpack"},
        timeout=None,
    ) as response:
        response.raise_for_status()
        async for chunk in response.aiter_bytes():
            if not chunk:
                continue
            if header_resolved:
                yield chunk
                continue
            prefix.extend(chunk)
            if len(prefix) < 4:
                continue
            if bytes(prefix[:4]) != b"RIFF":
                # Raw PCM, no header: emit buffered bytes and pass through.
                header_resolved = True
                yield bytes(prefix)
                prefix.clear()
                continue
            # RIFF header present: wait until the 'data' chunk, then emit PCM.
            offset = _pcm_offset(prefix)
            if offset is not None:
                header_resolved = True
                pcm = bytes(prefix[offset:])
                prefix.clear()
                if pcm:
                    yield pcm
