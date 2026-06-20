#!/usr/bin/env bash
# Sets up the two external inference servers needed by the audio pipeline:
#   - vLLM  → Director LLM (Llama-3.1-8B-Instruct) on GPU 0, port 30000
#   - Fish Speech 1.5 → TTS server on GPU 1, port 30001
#
# Run once from the repo root on a fresh GPU cluster (Ubuntu 22, CUDA 12.x).
# These are isolated from the services .venv to avoid dependency conflicts.
#
# Lessons learned (hardcoded to prevent re-discovering):
#   - vLLM needs ninja-build (apt) + ninja (pip) for FlashInfer JIT compilation
#   - fish-speech must be v1.5.1 — v2.0 changed the DAC hidden dim (512→1024),
#     making it incompatible with the fish-speech-1.5 model weights
#   - fish-speech deps must be installed with --no-deps then individually, because
#     the full `pip install -e .[stable]` backtracks on zstandard for 30+ minutes
#     and still fails. The explicit list below is exhaustive on purpose — most of
#     those modules are imported directly by fish-speech and are NOT transitive
#     deps of each other, so a partial list fails one ModuleNotFoundError at a time
#     (kui -> numpy -> audiotools -> dac -> pytorch_lightning -> funasr -> ...)
#   - fish-speech 1.5.1 server uses --llama-checkpoint-path + --decoder-checkpoint-path
#     (not --checkpoint-path, which was removed between v1.4 and v1.5.1)
#   - the TTS client (services/tts/fish_audio.py) must POST ormsgpack to /v1/tts —
#     fish-speech's native server has no OpenAI-style /v1/audio/speech route (404)
#   - torch 2.5.1+cu121 works for fish-speech 1.5 inference despite the pyproject.toml
#     saying torch==2.8.0 — 2.8.0 isn't available for cu121
#   - AudioGen.get_pretrained() must receive device='cuda' (not 'cuda:2') because
#     audiocraft passes device_type to torch.autocast which rejects 'cuda:N' format;
#     use torch.cuda.set_device(N) + 'cuda' instead (already fixed in audiogen.py)
#   - The Director calls vLLM with model='meta-llama/Meta-Llama-3.1-8B-Instruct' (with
#     the .1) — must match exactly what vLLM was started with (already fixed in model.py)
set -euo pipefail

# ── System deps ───────────────────────────────────────────────────────────────
echo "Installing system dependencies..."
# Refresh the apt cache first — fresh containers have an empty package list and
# `apt-get install` would otherwise fail to find ninja-build.
apt-get update -qq || echo "WARNING: 'apt-get update' failed — ninja-build install may fail"
apt-get install -y ninja-build

# ── vLLM venv ────────────────────────────────────────────────────────────────
echo ""
echo "Creating vLLM venv at /tmp/vllm-venv..."
python3 -m venv /tmp/vllm-venv
/tmp/vllm-venv/bin/pip install --upgrade pip --quiet

# ninja pip package required for FlashInfer JIT; ninja-build (apt) above isn't enough
/tmp/vllm-venv/bin/pip install --quiet ninja

# Pin vLLM to the 0.8.x line. Newer vLLM (0.9+) ships PyTorch built against
# CUDA 12.6/12.8/12.9, which fails on hosts whose NVIDIA driver only supports
# CUDA 12.4 with:
#   "RuntimeError: The NVIDIA driver on your system is too old (found 12040)"
# vLLM 0.8.5 bundles torch 2.6.0+cu124, which runs natively on a 12.4 driver.
# Bump this only after confirming the host driver supports the newer CUDA build.
/tmp/vllm-venv/bin/pip install --quiet "vllm==0.8.5.post1"

# vLLM 0.8.5 declares `transformers>=4.51.1` with no upper bound, so pip happily
# installs transformers 5.x. The 5.x tokenizer-backend refactor (TokenizersBackend)
# drops all_special_tokens_extended, which vLLM 0.8.5 still calls, crashing at
# tokenizer init. Pin to the 4.51.x line vLLM 0.8.5 was built against.
/tmp/vllm-venv/bin/pip install --quiet "transformers==4.51.3"

echo ""
echo "vLLM installed. Llama-3.1 is a gated model — authenticate with HuggingFace:"
echo "  /tmp/vllm-venv/bin/huggingface-cli login"

# ── Fish Speech venv ──────────────────────────────────────────────────────────
echo ""
echo "Creating Fish Speech venv at /tmp/fish-venv..."
python3 -m venv /tmp/fish-venv
/tmp/fish-venv/bin/pip install --upgrade pip --quiet

# Clone or update fish-speech, locked to v1.5.1
if [ ! -d /tmp/fish-speech ]; then
  echo "Cloning fish-speech..."
  git clone https://github.com/fishaudio/fish-speech /tmp/fish-speech
fi
cd /tmp/fish-speech
git fetch --tags
git checkout v1.5.1

# Install fish-speech package without its conflicting transitive deps
/tmp/fish-venv/bin/pip install -e . --no-deps --quiet

# torch 2.5.1 is the newest available for cu121; fish-speech pyproject.toml says
# 2.8.0 but that's cu124+ only and 2.5.1 is API-compatible for inference
/tmp/fish-venv/bin/pip install --quiet \
  "torch==2.5.1" "torchaudio==2.5.1" \
  --index-url https://download.pytorch.org/whl/cu121

# Runtime deps for fish-speech 1.5.1 inference.
#
# This is the COMPLETE set the server actually imports. Installing a subset
# triggers a long ModuleNotFoundError cascade at startup, one missing module at
# a time (kui -> numpy -> audiotools -> dac -> pytorch_lightning -> funasr -> ...),
# because most of these are imported directly by fish-speech and are NOT pulled
# in transitively by the packages above. Keep the list exhaustive so the server
# boots on the first try.
#
# transformers MUST stay <=4.57.3: newer (5.x) drops huggingface_hub<1.0 and the
# `huggingface-cli` entry point used below, and changes the model loading path.
/tmp/fish-venv/bin/pip install --quiet \
  "transformers<=4.57.3" \
  "pyrootutils" \
  "funasr" \
  "vector_quantize_pytorch==1.14.24" \
  "faster-whisper" \
  "lightning>=2.1.0" \
  "descript-audio-codec" \
  "descript-audiotools" \
  "einx[torch]==0.2.2" \
  "einops" \
  "opencc-python-reimplemented==0.1.7" \
  "resampy" \
  "librosa" \
  "numpy" \
  "safetensors" \
  "tiktoken" \
  "hydra-core" \
  "natsort" \
  "grpcio" \
  "pydub" \
  "loralib" \
  "zstandard" \
  "kui" \
  "msgpack" \
  "ormsgpack" \
  "loguru" \
  "pydantic" \
  "silero-vad" \
  "cachetools" \
  "rich" \
  "uvicorn[standard]"

# Download fish-speech-1.5 checkpoint weights
echo ""
echo "Downloading fish-speech-1.5 checkpoint (~3.5 GB)..."
/tmp/fish-venv/bin/huggingface-cli download \
  fishaudio/fish-speech-1.5 \
  --local-dir /tmp/fish-speech/checkpoints/fish-speech-1.5

cd -

echo ""
echo "============================================================"
echo "Inference server setup complete."
echo ""
echo "Start servers in two separate tmux panes:"
echo ""
echo "  Pane 1 (vLLM, GPU 0):"
echo "    bash scripts/start_inference.sh vllm"
echo ""
echo "  Pane 2 (Fish Speech TTS, GPU 1):"
echo "    bash scripts/start_inference.sh tts"
echo ""
echo "Then in pane 3:"
echo "    make start && make wait && make smoke && make e2e && make full"
echo "============================================================"
