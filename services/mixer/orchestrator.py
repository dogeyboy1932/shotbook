from __future__ import annotations

import asyncio
import os
from collections.abc import AsyncIterator

import httpx

from .audio_prompt import ParsedAudioPrompt, parse_audio_prompt
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
        async with client.stream(
            "POST", f"{TTS_SERVICE_URL}/synthesize", json=payload
        ) as resp:
            resp.raise_for_status()
            async for chunk in resp.aiter_bytes(chunk_size=4096):
                if chunk:
                    await chunk_queue.put(chunk)
    finally:
        await chunk_queue.put(None)


async def _fetch_sfx(client: httpx.AsyncClient, block: ScriptBlock) -> list[dict]:
    if not block.sfx_track:
        return []
    payload = {
        "sequence_id": block.sequence_id,
        "sfx_track": [cue.model_dump() for cue in block.sfx_track],
    }
    resp = await client.post(f"{SFX_SERVICE_URL}/generate", json=payload)
    resp.raise_for_status()
    data = resp.json()
    return data.get("cues", [])


async def _queue_to_async_iter(
    queue: asyncio.Queue[bytes | None],
) -> AsyncIterator[bytes]:
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


# ---------------------------------------------------------------------------
# Audio-prompt dispatch (multi-dialogue pipeline)
# ---------------------------------------------------------------------------


async def _collect_tts(
    client: httpx.AsyncClient,
    dialogue: str,
    speaker_id: str,
    sequence_id: str,
) -> bytes:
    """Call the TTS service for a single dialogue line and collect all PCM bytes."""
    payload = {
        "sequence_id": sequence_id,
        "speaker_id": speaker_id,
        "dialogue": dialogue,
    }
    chunks: list[bytes] = []
    async with client.stream(
        "POST", f"{TTS_SERVICE_URL}/synthesize", json=payload
    ) as resp:
        resp.raise_for_status()
        async for chunk in resp.aiter_bytes(chunk_size=4096):
            if chunk:
                chunks.append(chunk)
    return b"".join(chunks)


async def _collect_sfx_pcm(
    client: httpx.AsyncClient,
    sequence_id: str,
    sfx_track: list[dict],
) -> bytes:
    """Call the SFX service and return concatenated PCM for all cues.

    Returns raw s16le mono PCM at 44100 Hz (resampled from SFX output).
    """
    if not sfx_track:
        return b""

    payload = {
        "sequence_id": sequence_id,
        "sfx_track": sfx_track,
    }
    resp = await client.post(f"{SFX_SERVICE_URL}/generate", json=payload)
    resp.raise_for_status()
    data = resp.json()
    cues = data.get("cues", [])

    # Decode base64 WAV cues to raw PCM
    import audioop
    import base64
    import io
    import wave

    all_pcm = bytearray()
    for cue in cues:
        audio_b64 = cue.get("audio_b64", "")
        if not audio_b64:
            continue
        raw = base64.b64decode(audio_b64)
        with wave.open(io.BytesIO(raw)) as wf:
            src_rate = wf.getframerate()
            src_width = wf.getsampwidth()
            n_channels = wf.getnchannels()
            pcm = wf.readframes(wf.getnframes())

        if n_channels > 1:
            pcm = audioop.tomono(pcm, src_width, 0.5, 0.5)

        if src_width != 2:
            pcm = audioop.lin2lin(pcm, src_width, 2)

        if src_rate != 44100:
            pcm, _ = audioop.ratecv(pcm, 2, 1, src_rate, 44100, None)

        all_pcm.extend(pcm)

    return bytes(all_pcm)


async def dispatch_from_prompt(
    client: httpx.AsyncClient,
    prompt_text: str,
    book_id: str = "audio_prompt",
    gap_ms: int = 800,
) -> tuple[list[bytes], bytes, int, list[str], list[str]]:
    """Parse an audio_prompt and generate TTS + SFX for all elements.

    Lines from the same speaker are batched into a single TTS call so the
    voice stays consistent (Fish Speech's default voice varies per call).

    Returns
    -------
    dialogue_pcms:
        One PCM buffer per speaker group, in appearance order.
    sfx_pcm:
        Raw s16le PCM for ambient SFX.
    total_duration_ms:
        Estimated total duration including gaps.
    dialogue_texts:
        Original text for each group (for speech-rate normalization).
    speaker_names:
        Speaker name for each group (for logging).
    """
    parsed = parse_audio_prompt(prompt_text)

    # Group lines by speaker, preserving order of first appearance
    speaker_groups: list[
        tuple[str, list[str]]
    ] = []  # [(speaker, [text1, text2, ...]), ...]
    speaker_order: list[str] = []
    for line in parsed.dialogue_lines:
        if line.speaker not in speaker_order:
            speaker_order.append(line.speaker)
            speaker_groups.append((line.speaker, []))
        idx = speaker_order.index(line.speaker)
        speaker_groups[idx][1].append(line.text)

    # Generate one TTS call per speaker group
    dialogue_pcms: list[bytes] = []
    dialogue_texts: list[str] = []
    speaker_names: list[str] = []
    for speaker, texts in speaker_groups:
        speaker_id = f"character_{speaker.lower().replace(' ', '_')}_profile"
        seq_id = f"{book_id}_{speaker.lower().replace(' ', '_')}"
        # Join all lines for this speaker with natural pause markers
        combined_text = " ... ".join(texts)
        pcm = await _collect_tts(client, combined_text, speaker_id, seq_id)
        dur_s = len(pcm) / 2 / 44100
        print(
            f"[dispatch] {speaker} ({len(texts)} lines): {len(pcm)} bytes PCM ({dur_s:.1f}s)",
            flush=True,
        )
        dialogue_pcms.append(pcm)
        dialogue_texts.append(combined_text)
        speaker_names.append(speaker)

    # Generate SFX for ambient descriptions — one cue per sound
    sfx_task = None
    sfx_track = []
    if parsed.ambient_descriptions:
        # Each ambient description gets its own cue, spaced every 2s
        for t, desc in enumerate(parsed.ambient_descriptions):
            timestamp_ms = t * 2000  # stagger by 2s to give each its own slot
            sfx_track.append({"timestamp_ms": timestamp_ms, "prompt": desc})
        sfx_task = asyncio.create_task(
            _collect_sfx_pcm(client, f"{book_id}_ambient", sfx_track)
        )

    # Wait for SFX if requested (runs concurrently with last TTS call)
    sfx_pcm = await sfx_task if sfx_task else b""

    # Calculate total duration (dialogue + gaps)
    total_ms = 0
    for i, pcm in enumerate(dialogue_pcms):
        dur_ms = int(len(pcm) / 2 / 44100 * 1000)
        total_ms += dur_ms
        if i < len(dialogue_pcms) - 1:
            total_ms += gap_ms

    # Add a small trailing padding
    total_ms += 500

    return dialogue_pcms, sfx_pcm, total_ms, dialogue_texts, speaker_names
