#!/usr/bin/env bash
# NOTE: This script is Linux/WSL2-only — the mixer uses POSIX named pipes (mkfifo/pass_fds)
# which are not available on Windows or macOS.
set -e

# Prefer the services venv's uvicorn so `make start` works without first running
# `source .venv/bin/activate`. Fall back to PATH if the venv binary is absent
# (e.g. when the venv is already activated).
UVICORN=".venv/bin/uvicorn"
[ -x "$UVICORN" ] || UVICORN="uvicorn"

# Service ports are overridable so they can dodge ports already taken on the host
# (e.g. a provider's nginx squatting on 8001). Set these in .env to change them;
# `make start/wait/smoke` all read the same values.
DIRECTOR_PORT=${DIRECTOR_PORT:-8000}
TTS_PORT=${TTS_PORT:-8001}
SFX_PORT=${SFX_PORT:-8002}
MIXER_PORT=${MIXER_PORT:-8003}

SGLANG_DIRECTOR_URL=${SGLANG_DIRECTOR_URL:-http://localhost:30000} \
  "$UVICORN" services.director.main:app --host 0.0.0.0 --port "$DIRECTOR_PORT" &
PID_DIRECTOR=$!

SGLANG_TTS_URL=${SGLANG_TTS_URL:-http://localhost:30001} \
  "$UVICORN" services.tts.main:app --host 0.0.0.0 --port "$TTS_PORT" &
PID_TTS=$!

# Pin the SFX process to ONE physical GPU via CUDA_VISIBLE_DEVICES so AudioGen
# only ever sees cuda:0 (in every thread). This avoids audiocraft's autocast
# rejecting 'cuda:N' and the cross-thread device mismatch during generation.
# Accepts SFX_CUDA_DEVICE=2 or the legacy SFX_GPU_DEVICE=cuda:2 form.
SFX_CUDA_DEVICE=${SFX_CUDA_DEVICE:-${SFX_GPU_DEVICE:-cuda:2}}
SFX_CUDA_DEVICE=${SFX_CUDA_DEVICE#cuda:}
CUDA_VISIBLE_DEVICES=$SFX_CUDA_DEVICE \
  "$UVICORN" services.sfx.main:app --host 0.0.0.0 --port "$SFX_PORT" &
PID_SFX=$!

# The mixer always calls the LOCAL TTS/SFX services, so derive their URLs from
# the ports above unconditionally. Inline assignment overrides any inherited
# value (e.g. a stale TTS_SERVICE_URL=...:8001 left in .env from an older
# default), which would otherwise point the mixer at the wrong port.
TTS_SERVICE_URL=http://localhost:$TTS_PORT \
SFX_SERVICE_URL=http://localhost:$SFX_PORT \
  "$UVICORN" services.mixer.main:app --host 0.0.0.0 --port "$MIXER_PORT" &
PID_MIXER=$!

trap "kill $PID_DIRECTOR $PID_TTS $PID_SFX $PID_MIXER 2>/dev/null; wait" EXIT SIGINT SIGTERM
wait
