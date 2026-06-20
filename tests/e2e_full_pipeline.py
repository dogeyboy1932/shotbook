#!/usr/bin/env python3
"""
Full pipeline test: highlight text → Director → ScriptBlock → Mixer WebSocket → MP3.
Tests the complete user flow end-to-end.

Usage:
    python tests/e2e_full_pipeline.py
    python tests/e2e_full_pipeline.py --host 192.168.1.10
"""
import argparse
import asyncio
import json
import sys
import time

import httpx
import websockets

HIGHLIGHT = "I heard all things in the heaven and in the earth. How then am I mad?"
CONTEXT_CHUNKS = [
    "The disease had sharpened my senses — not destroyed — not dulled them.",
    "I heard many things in hell. How then am I mad?",
    "Above all was the sense of hearing acute. I heard all things in heaven and earth.",
]
BOOK_ID = "poe-the-tell-tale-heart"


async def run(host: str, director_port: int, mixer_port: int, output: str) -> bool:
    director_url = f"http://{host}:{director_port}"
    mixer_ws = f"ws://{host}:{mixer_port}/ws/audio"
    t0 = time.monotonic()

    # ── Step 1: Director ─────────────────────────────────────────────────────
    print(f"Step 1: POST {director_url}/script")
    async with httpx.AsyncClient(timeout=30.0) as client:
        resp = await client.post(
            f"{director_url}/script",
            json={
                "highlight": HIGHLIGHT,
                "context_chunks": CONTEXT_CHUNKS,
                "book_id": BOOK_ID,
                "speaker_hint": "narrator",
            },
        )
        if resp.status_code != 200:
            print(f"FAIL  Director returned {resp.status_code}: {resp.text}")
            return False
        block = resp.json()

    t_director = time.monotonic()
    print(f"  Director latency:    {(t_director - t0)*1000:6.0f}ms")
    print(f"  sequence_id:         {block['sequence_id']}")
    print(f"  speaker_id:          {block['speaker_id']}")
    print(f"  dialogue:            {block['dialogue'][:80]}")
    print(f"  sfx_track:           {len(block['sfx_track'])} cue(s)")
    for cue in block["sfx_track"]:
        print(f"    t={cue['timestamp_ms']}ms  {cue['prompt']}")

    # ── Step 2: Mixer WebSocket ───────────────────────────────────────────────
    print(f"\nStep 2: WebSocket {mixer_ws}")
    ttfab: float | None = None
    total_bytes = 0

    try:
        async with websockets.connect(mixer_ws, max_size=None, open_timeout=10) as ws:
            await ws.send(json.dumps(block))
            t_sent = time.monotonic()
            print(f"  ScriptBlock sent:    {(t_sent - t0)*1000:6.0f}ms from highlight")

            with open(output, "wb") as f:
                async for message in ws:
                    if isinstance(message, bytes):
                        if ttfab is None:
                            ttfab = time.monotonic()
                        f.write(message)
                        total_bytes += len(message)
                    elif isinstance(message, str):
                        data = json.loads(message)
                        if data.get("done"):
                            break
                        if data.get("error"):
                            print(f"\nERROR from mixer: {data['error']}")
                            return False
    except (websockets.exceptions.WebSocketException, OSError) as exc:
        print(f"\nWebSocket error: {exc}")
        return False

    t_end = time.monotonic()
    ttfab_ms = (ttfab - t0) * 1000 if ttfab else None
    total_ms = (t_end - t0) * 1000

    print()
    print(f"  TTFAB (from highlight): {ttfab_ms:6.0f}ms")
    print(f"  Total elapsed:          {total_ms:6.0f}ms")
    print(f"  Audio received:         {total_bytes/1024:6.1f}KB")
    print(f"  Saved to:               {output}")
    print()

    # Director adds ~200-400ms on top; full-flow TTFAB ~600-800ms is expected
    passed = ttfab_ms is not None and total_bytes > 0
    if passed:
        print(f"PASS  Full pipeline produced {total_bytes//1024}KB of audio")
        if ttfab_ms and ttfab_ms > 600:
            print(f"NOTE  TTFAB {ttfab_ms:.0f}ms (Director on hot path adds latency; Mixer alone targets <600ms)")
    else:
        print("FAIL  No audio received")
    return passed


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--host", default="localhost")
    parser.add_argument("--director-port", type=int, default=8000)
    parser.add_argument("--mixer-port", type=int, default=8003)
    parser.add_argument("--output", default="/tmp/full_pipeline.mp3")
    args = parser.parse_args()

    passed = asyncio.run(run(args.host, args.director_port, args.mixer_port, args.output))
    sys.exit(0 if passed else 1)


if __name__ == "__main__":
    main()
