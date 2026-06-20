import asyncio
import audioop
import base64
import io
import os
import subprocess
import sys
import wave
from collections.abc import AsyncIterator

TARGET_RATE = 44100
TARGET_WIDTH = 2  # s16le = 2 bytes per sample


def _decode_sfx_cue_to_pcm(audio_b64: str) -> bytes:
    raw = base64.b64decode(audio_b64)
    with wave.open(io.BytesIO(raw)) as wf:
        src_rate = wf.getframerate()
        src_width = wf.getsampwidth()
        n_channels = wf.getnchannels()
        pcm = wf.readframes(wf.getnframes())

    if n_channels > 1:
        pcm = audioop.tomono(pcm, src_width, 0.5, 0.5)

    if src_width != TARGET_WIDTH:
        pcm = audioop.lin2lin(pcm, src_width, TARGET_WIDTH)

    if src_rate != TARGET_RATE:
        pcm, _ = audioop.ratecv(pcm, TARGET_WIDTH, 1, src_rate, TARGET_RATE, None)

    return pcm


def _build_ffmpeg_cmd(vocal_fd: int, sfx_fd: int) -> list[str]:
    return [
        "ffmpeg",
        "-f",
        "s16le",
        "-ar",
        "44100",
        "-ac",
        "1",
        "-i",
        f"pipe:{vocal_fd}",
        "-f",
        "s16le",
        "-ar",
        "44100",
        "-ac",
        "1",
        "-i",
        f"pipe:{sfx_fd}",
        "-filter_complex",
        "[0:a]volume=1.0[vocal];[1:a]volume=0.55[ambient];[vocal][ambient]amix=inputs=2:duration=first[out]",
        "-map",
        "[out]",
        "-f",
        "mp3",
        "-q:a",
        "4",
        "pipe:1",
        "-loglevel",
        "error",
    ]


async def _write_all_to_fd(
    fd: int, data: bytes, loop: asyncio.AbstractEventLoop
) -> None:
    view = memoryview(data)
    offset = 0
    while offset < len(view):
        written = await loop.run_in_executor(None, os.write, fd, bytes(view[offset:]))
        offset += written


async def _pump_vocal(
    fd: int,
    vocal_chunks: AsyncIterator[bytes],
    loop: asyncio.AbstractEventLoop,
) -> None:
    try:
        async for chunk in vocal_chunks:
            if chunk:
                await _write_all_to_fd(fd, chunk, loop)
    finally:
        os.close(fd)


async def _pump_sfx(
    fd: int,
    sfx_b64_cues: list[dict],
    loop: asyncio.AbstractEventLoop,
) -> None:
    try:
        for cue in sfx_b64_cues:
            audio_b64 = cue.get("audio_b64", "")
            if not audio_b64:
                continue
            pcm = await loop.run_in_executor(None, _decode_sfx_cue_to_pcm, audio_b64)
            await _write_all_to_fd(fd, pcm, loop)
    finally:
        os.close(fd)


def _read_ffmpeg_output(
    proc: subprocess.Popen, queue: asyncio.Queue, loop: asyncio.AbstractEventLoop
) -> None:
    try:
        while True:
            chunk = proc.stdout.read(4096)
            if not chunk:
                break
            asyncio.run_coroutine_threadsafe(queue.put(chunk), loop)
    finally:
        asyncio.run_coroutine_threadsafe(queue.put(None), loop)


def _read_ffmpeg_stderr(proc: subprocess.Popen, sink: list[bytes]) -> None:
    try:
        data = proc.stderr.read()
        if data:
            sink.append(data)
    except Exception:
        pass


async def mix_audio(
    vocal_chunks: AsyncIterator[bytes],
    sfx_b64_cues: list[dict],
) -> AsyncIterator[bytes]:
    loop = asyncio.get_running_loop()

    vocal_r, vocal_w = os.pipe()
    sfx_r, sfx_w = os.pipe()

    cmd = _build_ffmpeg_cmd(vocal_r, sfx_r)

    proc = subprocess.Popen(
        cmd,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        pass_fds=(vocal_r, sfx_r),
        close_fds=True,
    )

    # Parent closes read ends — FFmpeg owns them now
    os.close(vocal_r)
    os.close(sfx_r)

    mp3_queue: asyncio.Queue[bytes | None] = asyncio.Queue(maxsize=64)

    reader_future = loop.run_in_executor(
        None, _read_ffmpeg_output, proc, mp3_queue, loop
    )
    stderr_sink: list[bytes] = []
    stderr_future = loop.run_in_executor(None, _read_ffmpeg_stderr, proc, stderr_sink)

    vocal_task = asyncio.create_task(_pump_vocal(vocal_w, vocal_chunks, loop))
    sfx_task = asyncio.create_task(_pump_sfx(sfx_w, sfx_b64_cues, loop))

    try:
        while True:
            chunk = await mp3_queue.get()
            if chunk is None:
                break
            yield chunk
    finally:
        vocal_task.cancel()
        sfx_task.cancel()
        await asyncio.gather(vocal_task, sfx_task, return_exceptions=True)
        await reader_future
        await stderr_future
        proc.wait()
        err = b"".join(stderr_sink).decode("utf-8", "replace").strip()
        # With `-loglevel error` anything on stderr is a real failure; surface it.
        if proc.returncode or err:
            print(
                f"[mixer] ffmpeg rc={proc.returncode}: {err}",
                file=sys.stderr,
                flush=True,
            )
