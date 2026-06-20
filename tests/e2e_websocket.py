#!/usr/bin/env python3
"""
WebSocket end-to-end test: ScriptBlock → Mixer → mixed MP3.
Measures time-to-first-audio-byte (TTFAB). Target: <600ms.

Usage:
    python tests/e2e_websocket.py
    python tests/e2e_websocket.py --host 192.168.1.10 --port 8003
"""
import argparse
import asyncio
import json
import sys
import time

import websockets

SCRIPT_BLOCK = {
    "sequence_id": "e2e_test_001",
    "speaker_id": "narrator_default",
    "dialogue": "[grimly] The shadow is approaching. Run!",
    "sfx_track": [
        {"timestamp_ms": 0, "prompt": "deep low frequency rumbling tremor"},
        {"timestamp_ms": 1200, "prompt": "stone crashing impact"},
    ],
}

TTFAB_TARGET_MS = 600


async def run(host: str, port: int, output: str) -> bool:
    uri = f"ws://{host}:{port}/ws/audio"
    print(f"Connecting to {uri} ...")
    t_start = time.monotonic()

    try:
        async with websockets.connect(uri, max_size=None, open_timeout=10) as ws:
            await ws.send(json.dumps(SCRIPT_BLOCK))
            t_sent = time.monotonic()
            print(f"  ScriptBlock sent:    {(t_sent - t_start)*1000:6.0f}ms")

            total_bytes = 0
            ttfab: float | None = None

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
                            print(f"\nERROR from server: {data['error']}")
                            return False

    except websockets.exceptions.WebSocketException as exc:
        print(f"\nWebSocket error: {exc}")
        return False
    except OSError as exc:
        print(f"\nConnection failed: {exc}")
        return False

    t_end = time.monotonic()
    ttfab_ms = (ttfab - t_start) * 1000 if ttfab else None
    total_ms = (t_end - t_start) * 1000

    print(f"  Time-to-first-byte:  {ttfab_ms:6.0f}ms  (target: <{TTFAB_TARGET_MS}ms)")
    print(f"  Total time:          {total_ms:6.0f}ms")
    print(f"  Audio received:      {total_bytes/1024:6.1f}KB")
    print(f"  Saved to:            {output}")

    passed = ttfab_ms is not None and ttfab_ms < TTFAB_TARGET_MS
    print()
    if passed:
        print(f"PASS  TTFAB {ttfab_ms:.0f}ms < {TTFAB_TARGET_MS}ms")
    else:
        print(f"FAIL  TTFAB {ttfab_ms:.0f}ms >= {TTFAB_TARGET_MS}ms")
    return passed


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--host", default="localhost")
    parser.add_argument("--port", type=int, default=8003)
    parser.add_argument("--output", default="/tmp/e2e_output.mp3")
    args = parser.parse_args()

    passed = asyncio.run(run(args.host, args.port, args.output))
    sys.exit(0 if passed else 1)


if __name__ == "__main__":
    main()
