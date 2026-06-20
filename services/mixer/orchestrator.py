import asyncio
import os
from collections.abc import AsyncIterator

import httpx

from .schema import ScriptBlock

TTS_SERVICE_URL = os.getenv("TTS_SERVICE_URL", "http://localhost:8001")
SFX_SERVICE_URL = os.getenv("SFX_SERVICE_URL", "http://localhost:8002")


async def _stream_tts(
    client: httpx.AsyncClient,
    block: ScriptBlock,
    chunk_queue: asyncio.Queue[bytes | None],
) -> None:
    payload = {
        "sequence_id": block.sequence_id,
        "speaker_id": block.speaker_id,
        "dialogue": block.dialogue,
    }
    try:
        async with client.stream("POST", f"{TTS_SERVICE_URL}/synthesize", json=payload) as resp:
            resp.raise_for_status()
            async for chunk in resp.aiter_bytes(chunk_size=4096):
                if chunk:
                    await chunk_queue.put(chunk)
    finally:
        await chunk_queue.put(None)


async def _fetch_sfx(client: httpx.AsyncClient, block: ScriptBlock) -> list[dict]:
    if not block.sfx_track:
        return []
    payload = {"cues": [cue.model_dump() for cue in block.sfx_track]}
    resp = await client.post(f"{SFX_SERVICE_URL}/generate", json=payload)
    resp.raise_for_status()
    data = resp.json()
    return data.get("cues", [])


async def _queue_to_async_iter(queue: asyncio.Queue[bytes | None]) -> AsyncIterator[bytes]:
    while True:
        chunk = await queue.get()
        if chunk is None:
            return
        yield chunk


async def dispatch(
    client: httpx.AsyncClient,
    block: ScriptBlock,
) -> tuple[AsyncIterator[bytes], list[dict]]:
    chunk_queue: asyncio.Queue[bytes | None] = asyncio.Queue(maxsize=128)

    tts_task = asyncio.create_task(_stream_tts(client, block, chunk_queue))
    sfx_cues = await _fetch_sfx(client, block)

    # TTS streams concurrently; drain it via the queue iterator
    vocal_stream = _queue_to_async_iter(chunk_queue)

    # Ensure tts_task propagates exceptions if it fails
    def _on_done(fut: asyncio.Future) -> None:
        if fut.exception():
            asyncio.get_event_loop().call_exception_handler(
                {"message": "TTS task failed", "exception": fut.exception()}
            )

    tts_task.add_done_callback(_on_done)

    return vocal_stream, sfx_cues
