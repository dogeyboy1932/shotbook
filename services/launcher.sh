#!/usr/bin/env bash
# NOTE: This script is Linux/WSL2-only — the mixer uses POSIX named pipes (mkfifo/pass_fds)
# which are not available on Windows or macOS.
set -e

SGLANG_DIRECTOR_URL=${SGLANG_DIRECTOR_URL:-http://localhost:30000} \
  uvicorn services.director.main:app --host 0.0.0.0 --port 8000 &
PID_DIRECTOR=$!

SGLANG_TTS_URL=${SGLANG_TTS_URL:-http://localhost:30001} \
  uvicorn services.tts.main:app --host 0.0.0.0 --port 8001 &
PID_TTS=$!

SFX_GPU_DEVICE=${SFX_GPU_DEVICE:-cuda:2} \
  uvicorn services.sfx.main:app --host 0.0.0.0 --port 8002 &
PID_SFX=$!

TTS_SERVICE_URL=http://localhost:8001 \
SFX_SERVICE_URL=http://localhost:8002 \
  uvicorn services.mixer.main:app --host 0.0.0.0 --port 8003 &
PID_MIXER=$!

trap "kill $PID_DIRECTOR $PID_TTS $PID_SFX $PID_MIXER 2>/dev/null; wait" EXIT SIGINT SIGTERM
wait
