"""Multi-dialogue audio mixer using ffmpeg.

Unlike ``pipe_mixer.py`` which streams a single vocal track directly into
ffmpeg, this module handles multiple dialogue lines with precise timing.
It collects all TTS output first, then builds a single ffmpeg command that
sequences dialogues with correct delays and mixes in ambient SFX.
"""

from __future__ import annotations

import asyncio
import os
import subprocess
import sys
from collections.abc import AsyncIterator

TARGET_RATE = 44100
TARGET_WIDTH = 2  # s16le = 2 bytes per sample
GAP_BETWEEN_LINES_MS = 800  # silence between consecutive dialogue lines


def _build_filter_cmd(
    dialogue_pcms: list[bytes],
    durations_ms: list[int],
    delays_ms: list[int],
    sfx_pcm: bytes | None,
    total_duration_ms: int,
) -> list[str]:
    """Build the ffmpeg command with filter_complex."""
    cmd = ["ffmpeg", "-y"]

    n_dialogue = len(dialogue_pcms)
    has_sfx = sfx_pcm is not None and len(sfx_pcm) > 0

    for i in range(n_dialogue):
        cmd += ["-f", "s16le", "-ar", str(TARGET_RATE), "-ac", "1", "-i", f"pipe:{i}"]

    if has_sfx:
        sfx_idx = n_dialogue
        cmd += ["-f", "s16le", "-ar", str(TARGET_RATE), "-ac", "1", "-i", f"pipe:{sfx_idx}"]

    filters = []

    for i, delay_ms in enumerate(delays_ms):
        filters.append(f"[{i}:a]adelay={delay_ms}|{delay_ms}[d{i}]")

    dialogue_inputs = " ".join(f"[d{i}]" for i in range(n_dialogue))
    filters.append(
        f"{dialogue_inputs}amix=inputs={n_dialogue}:duration=longest"
        f":dropout_transition=0[vocal]"
    )

    if has_sfx:
        sfx_idx = n_dialogue
        target_sec = total_duration_ms / 1000.0
        filters.append(
            f"[{sfx_idx}:a]aloop=loop=-1:size=2e+09,"
            f"atrim=duration={target_sec}[ambient]"
        )
        filters.append(
            "[vocal][ambient]amix=inputs=2:duration=first"
            ":weights=1 0.55[out]"
        )
        output_label = "[out]"
    else:
        target_sec = total_duration_ms / 1000.0
        filters.append(f"[vocal]atrim=duration={target_sec}[out]")
        output_label = "[out]"

    filter_str = ";".join(filters)
    cmd += ["-filter_complex", filter_str]

    # Output: AAC in MP4 container (compatible with MP4 video muxing)
    cmd += [
        "-map", output_label,
        "-c:a", "aac",
        "-b:a", "192k",
        "-f", "mp4",
        "pipe:1",
        "-loglevel", "error",
    ]
    return cmd


async def _write_all_to_fd(
    fd: int, data: bytes, loop: asyncio.AbstractEventLoop
) -> None:
    """Write all bytes to a file descriptor, handling partial writes."""
    view = memoryview(data)
    offset = 0
    while offset < len(view):
        written = await loop.run_in_executor(
            None, os.write, fd, bytes(view[offset:])
        )
        offset += written


def _read_ffmpeg_output(
    proc: subprocess.Popen,
    queue: asyncio.Queue,
    loop: asyncio.AbstractEventLoop,
) -> None:
    """Read ffmpeg stdout into a queue (runs in executor thread)."""
    try:
        while True:
            chunk = proc.stdout.read(4096)
            if not chunk:
                break
            asyncio.run_coroutine_threadsafe(queue.put(chunk), loop)
    finally:
        asyncio.run_coroutine_threadsafe(queue.put(None), loop)


def _read_ffmpeg_stderr(proc: subprocess.Popen, sink: list[bytes]) -> None:
    """Read ffmpeg stderr for error reporting."""
    try:
        data = proc.stderr.read()
        if data:
            sink.append(data)
    except Exception:
        pass


async def mix_multi_dialogue(
    dialogue_pcms: list[bytes],
    sfx_pcm: bytes | None,
    total_duration_ms: int,
) -> AsyncIterator[bytes]:
    """Mix multiple dialogue lines with ambient SFX and stream the result.

    Parameters
    ----------
    dialogue_pcms:
        Raw s16le PCM for each dialogue line, in order.
    sfx_pcm:
        Raw s16le PCM for ambient SFX (may be None).
    total_duration_ms:
        Total desired output duration in milliseconds.

    Yields
    ------
    Chunks of AAC-encoded audio in an MP4 container.
    """
    loop = asyncio.get_running_loop()
    n_dialogue = len(dialogue_pcms)
    has_sfx = sfx_pcm is not None and len(sfx_pcm) > 0

    # Calculate per-line durations and delays
    durations_ms = []
    for pcm in dialogue_pcms:
        num_samples = len(pcm) // TARGET_WIDTH
        durations_ms.append(int(num_samples / TARGET_RATE * 1000))

    delays_ms = []
    cumulative = 0
    for i, dur in enumerate(durations_ms):
        delays_ms.append(cumulative)
        cumulative += dur + (
            GAP_BETWEEN_LINES_MS if i < len(durations_ms) - 1 else 0
        )

    cmd = _build_filter_cmd(
        dialogue_pcms, durations_ms, delays_ms,
        sfx_pcm, total_duration_ms,
    )

    # Create pipe pairs for each input
    total_inputs = n_dialogue + (1 if has_sfx else 0)
    pipes = [os.pipe() for _ in range(total_inputs)]

    pass_fds = [r for r, w in pipes]
    proc = subprocess.Popen(
        cmd,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        pass_fds=pass_fds,
        close_fds=True,
    )

    # Close read ends in parent (ffmpeg owns them)
    for r, w in pipes:
        os.close(r)

    # Start writing PCM to pipe write ends
    writers = []
    for i, pcm in enumerate(dialogue_pcms):
        w_fd = pipes[i][1]
        writers.append(asyncio.create_task(_write_all_to_fd(w_fd, pcm, loop)))

    if has_sfx:
        w_fd = pipes[n_dialogue][1]
        writers.append(asyncio.create_task(_write_all_to_fd(w_fd, sfx_pcm, loop)))

    # Close write ends after all data is written
    async def _close_pipes():
        await asyncio.gather(*writers)
        for _, w in pipes:
            try:
                os.close(w)
            except OSError:
                pass

    close_task = asyncio.create_task(_close_pipes())

    # Read ffmpeg output
    out_queue: asyncio.Queue[bytes | None] = asyncio.Queue(maxsize=64)
    reader_future = loop.run_in_executor(
        None, _read_ffmpeg_output, proc, out_queue, loop
    )
    stderr_sink: list[bytes] = []
    stderr_future = loop.run_in_executor(
        None, _read_ffmpeg_stderr, proc, stderr_sink
    )

    try:
        while True:
            chunk = await out_queue.get()
            if chunk is None:
                break
            yield chunk
    finally:
        for w in writers:
            w.cancel()
        close_task.cancel()
        await asyncio.gather(*writers, close_task, return_exceptions=True)
        await reader_future
        await stderr_future
        proc.wait()
        err = b"".join(stderr_sink).decode("utf-8", "replace").strip()
        if proc.returncode or err:
            print(
                f"[multi_mixer] ffmpeg rc={proc.returncode}: {err}",
                file=sys.stderr,
                flush=True,
            )
