"""Multi-dialogue audio mixer using ffmpeg.

All dialogue lines are concatenated into a single PCM stream with silence
gaps (avoids the amix+adelay sync problem where amix waits for all inputs).
If ambient SFX is present, it goes to a second pipe and is mixed in.
"""

from __future__ import annotations

import asyncio
import os
import subprocess
import sys
from array import array
from collections.abc import AsyncIterator

TARGET_RATE = 44100
TARGET_WIDTH = 2  # s16le = 2 bytes per sample
SFX_WEIGHT = 0.22  # ambient volume relative to vocal
TARGET_PEAK = 0.85  # normalize dialogue to 85% of full scale


def _build_silence_pcm(duration_ms: int) -> bytes:
    """Return silent s16le mono PCM for the given duration."""
    num_samples = int(TARGET_RATE * duration_ms / 1000)
    return bytes(num_samples * TARGET_WIDTH)


def _normalize_pcm(pcm: bytes, target_peak: float = TARGET_PEAK) -> bytes:
    """Peak-normalize s16le mono PCM to target_peak of full scale (32767).

    Leaves silence unchanged. All dialogue lines are normalized independently
    so the same character's voice has consistent perceived loudness across a
    multi-line scene.
    """
    if len(pcm) < 2:
        return pcm
    samples = array("h")
    samples.frombytes(pcm)
    peak = max(max(samples), abs(min(samples)))
    if peak == 0:
        return pcm
    scale = 32767 * target_peak / peak
    normalized = array("h", (int(s * scale) for s in samples))
    return normalized.tobytes()


def _count_words(text: str) -> int:
    """Count words in dialogue text for speech-rate calculation."""
    return len(text.split())


def _adjust_line_tempo(pcm: bytes, factor: float) -> bytes:
    """Time-stretch s16le PCM using ffmpeg atempo (preserves pitch)."""
    if abs(factor - 1.0) < 0.02:
        return pcm
    factor = max(0.5, min(2.0, factor))
    proc = subprocess.run(
        [
            "ffmpeg",
            "-f",
            "s16le",
            "-ar",
            str(TARGET_RATE),
            "-ac",
            "1",
            "-i",
            "pipe:0",
            "-filter:a",
            f"atempo={factor:.3f}",
            "-f",
            "s16le",
            "-ac",
            "1",
            "-ar",
            str(TARGET_RATE),
            "pipe:1",
            "-loglevel",
            "error",
        ],
        input=pcm,
        capture_output=True,
    )
    if proc.returncode != 0:
        print(f"[multi_mixer] atempo {factor:.2f}x failed, using original", flush=True)
        return pcm
    return proc.stdout


def _normalize_speech_rates(
    dialogue_pcms: list[bytes],
    dialogue_texts: list[str],
) -> list[bytes]:
    """Adjust each line tempo so all speakers have roughly the same WPS."""
    if len(dialogue_pcms) <= 1:
        return list(dialogue_pcms)

    wps_list: list[float] = []
    for pcm, text in zip(dialogue_pcms, dialogue_texts):
        words = _count_words(text)
        dur_s = len(pcm) / TARGET_WIDTH / TARGET_RATE
        wps = words / dur_s if dur_s > 0 else 0
        wps_list.append(wps)

    sorted_wps = sorted(wps_list)
    mid = len(sorted_wps) // 2
    target_wps = sorted_wps[mid]

    if target_wps <= 0:
        return list(dialogue_pcms)

    print(
        f"[multi_mixer] WPS per line: {[round(w, 1) for w in wps_list]}, "
        f"target={target_wps:.1f}",
        flush=True,
    )

    result: list[bytes] = []
    for pcm, wps in zip(dialogue_pcms, wps_list):
        ratio = wps / target_wps if target_wps > 0 else 1.0
        if abs(ratio - 1.0) > 0.15:
            factor = 1.0 / ratio
            pcm = _adjust_line_tempo(pcm, factor)
        result.append(pcm)

    return result


def _concat_dialogues(dialogue_pcms: list[bytes], gap_ms: int) -> bytes:
    """Concatenate dialogue PCMs with silence gaps, normalizing each line."""
    parts: list[bytes] = []
    for i, pcm in enumerate(dialogue_pcms):
        if i > 0:
            parts.append(_build_silence_pcm(gap_ms))
        parts.append(_normalize_pcm(pcm))
    return b"".join(parts)


def _build_filter_cmd(
    vocal_fd: int,
    sfx_fd: int | None,
    total_duration_ms: int,
    speed: float = 1.0,
) -> list[str]:
    """Build ffmpeg command with at most 2 inputs: vocal + ambient."""
    cmd = ["ffmpeg", "-y"]

    cmd += [
        "-f",
        "s16le",
        "-ar",
        str(TARGET_RATE),
        "-ac",
        "1",
        "-i",
        f"pipe:{vocal_fd}",
    ]

    has_sfx = sfx_fd is not None
    if has_sfx:
        cmd += [
            "-f",
            "s16le",
            "-ar",
            str(TARGET_RATE),
            "-ac",
            "1",
            "-i",
            f"pipe:{sfx_fd}",
        ]

    filters = []
    target_sec = total_duration_ms / 1000.0

    if has_sfx:
        if speed != 1.0:
            filters.append(f"[0:a]atempo={speed}[vocal]")
        else:
            filters.append("[0:a]anull[vocal]")
        filters.append(f"[1:a]atrim=duration={target_sec}[ambient]")
        filters.append(
            f"[vocal][ambient]amix=inputs=2:duration=first:weights=1 {SFX_WEIGHT}[out]"
        )
    else:
        out_filter = f"[0:a]atrim=duration={target_sec}"
        if speed != 1.0:
            out_filter += f",atempo={speed}"
        out_filter += "[out]"
        filters.append(out_filter)

    filter_str = ";".join(filters)
    cmd += ["-filter_complex", filter_str]

    cmd += [
        "-map",
        "[out]",
        "-c:a",
        "libmp3lame",
        "-q:a",
        "4",
        "-f",
        "mp3",
        "pipe:1",
        "-loglevel",
        "error",
    ]
    return cmd


async def _write_all_to_fd(
    fd: int, data: bytes, loop: asyncio.AbstractEventLoop
) -> None:
    view = memoryview(data)
    offset = 0
    while offset < len(view):
        written = await loop.run_in_executor(None, os.write, fd, bytes(view[offset:]))
        offset += written


def _read_ffmpeg_output(
    proc: subprocess.Popen,
    queue: asyncio.Queue,
    loop: asyncio.AbstractEventLoop,
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


async def mix_multi_dialogue(
    dialogue_pcms: list[bytes],
    sfx_pcm: bytes | None,
    total_duration_ms: int,
    gap_ms: int = 800,
    speed: float = 1.0,
    dialogue_texts: list[str] | None = None,
) -> AsyncIterator[bytes]:
    loop = asyncio.get_running_loop()
    has_sfx = sfx_pcm is not None and len(sfx_pcm) > 0
    n_dialogue = len(dialogue_pcms)

    # Normalize speech rates before concatenation
    if dialogue_texts and len(dialogue_texts) == n_dialogue:
        dialogue_pcms = _normalize_speech_rates(dialogue_pcms, dialogue_texts)

    # Combine all dialogues + silence gaps into one contiguous PCM buffer
    combined_vocal = _concat_dialogues(dialogue_pcms, gap_ms)
    vocal_samples = len(combined_vocal) // TARGET_WIDTH
    vocal_dur_ms = int(vocal_samples / TARGET_RATE * 1000)

    # Loop SFX in Python to fill total duration (avoids ffmpeg aloop pipe issues)
    if has_sfx:
        sfx_samples = len(sfx_pcm) // TARGET_WIDTH  # type: ignore[arg-type]
        target_samples = int(TARGET_RATE * total_duration_ms / 1000)
        if sfx_samples > 0 and target_samples > sfx_samples:
            repeats = (target_samples // sfx_samples) + 1
            sfx_pcm = sfx_pcm * repeats  # type: ignore[operator]
            sfx_pcm = sfx_pcm[: target_samples * TARGET_WIDTH]  # type: ignore[index]

    print(
        f"[multi_mixer] {n_dialogue} lines -> combined {len(combined_vocal)} bytes"
        f" ({vocal_dur_ms}ms), total={total_duration_ms}ms, gap={gap_ms}ms, sfx={has_sfx}",
        flush=True,
    )

    # One pipe for vocal, optionally one for SFX
    vocal_r, vocal_w = os.pipe()
    sfx_r, sfx_w = (None, None)
    if has_sfx:
        sfx_r, sfx_w = os.pipe()

    cmd = _build_filter_cmd(vocal_r, sfx_r, total_duration_ms, speed)
    pass_fds = [vocal_r]
    if sfx_r is not None:
        pass_fds.append(sfx_r)

    proc = subprocess.Popen(
        cmd,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        pass_fds=pass_fds,
        close_fds=True,
    )

    os.close(vocal_r)
    if sfx_r is not None:
        os.close(sfx_r)

    writers = [asyncio.create_task(_write_all_to_fd(vocal_w, combined_vocal, loop))]
    if has_sfx and sfx_w is not None:
        writers.append(asyncio.create_task(_write_all_to_fd(sfx_w, sfx_pcm, loop)))

    async def _close_pipes():
        await asyncio.gather(*writers)
        try:
            os.close(vocal_w)
        except OSError:
            pass
        if sfx_w is not None:
            try:
                os.close(sfx_w)
            except OSError:
                pass

    close_task = asyncio.create_task(_close_pipes())

    out_queue: asyncio.Queue[bytes | None] = asyncio.Queue(maxsize=64)
    reader_future = loop.run_in_executor(
        None, _read_ffmpeg_output, proc, out_queue, loop
    )
    stderr_sink: list[bytes] = []
    stderr_future = loop.run_in_executor(None, _read_ffmpeg_stderr, proc, stderr_sink)

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
