#!/usr/bin/env bash
# Full setup for LoreStream AI audio pipeline on a Linux GPU cluster.
# Must be run from the repo root.
set -euo pipefail

# ── Prerequisites ────────────────────────────────────────────────────────────
echo "Checking prerequisites..."

if ! command -v python3 &>/dev/null; then
  echo "ERROR: python3 not found"; exit 1
fi

PYTHON_MIN="3.10"
python3 -c "import sys; v=sys.version_info; exit(0 if (v.major,v.minor)>=(3,10) else 1)" \
  || { echo "ERROR: Python 3.10+ required (got $(python3 --version))"; exit 1; }

if ! command -v ffmpeg &>/dev/null; then
  echo "Installing ffmpeg..."
  apt-get install -y ffmpeg 2>/dev/null || { echo "ERROR: ffmpeg not found and auto-install failed. Run: apt-get install -y ffmpeg"; exit 1; }
fi

ffmpeg -version 2>&1 | head -1

# ── Python venv ──────────────────────────────────────────────────────────────
echo ""
echo "Creating virtual environment..."

# Ensure python3-venv is available (missing on many minimal Ubuntu installs)
PYVER=$(python3 -c "import sys; print(f'{sys.version_info.major}.{sys.version_info.minor}')")
if ! python3 -m venv --help &>/dev/null 2>&1; then
  echo "Installing python${PYVER}-venv..."
  apt-get install -y "python${PYVER}-venv" || { echo "ERROR: could not install python${PYVER}-venv"; exit 1; }
fi

python3 -m venv .venv
source .venv/bin/activate
pip install --upgrade pip --quiet

# ── Core service dependencies ────────────────────────────────────────────────
echo "Installing core service dependencies..."
pip install --quiet \
  "fastapi>=0.111.0" \
  "uvicorn[standard]>=0.29.0" \
  "pydantic>=2.0.0" \
  "httpx>=0.27.0" \
  "websockets>=12.0"

# ── System build deps for PyAV (required by audiocraft) ─────────────────────
echo ""
echo "Installing system build dependencies for audiocraft..."
apt-get install -y --quiet \
  pkg-config \
  "python${PYVER}-dev" \
  libavformat-dev \
  libavcodec-dev \
  libavdevice-dev \
  libavutil-dev \
  libavfilter-dev \
  libswscale-dev \
  libswresample-dev

# ── PyTorch + AudioCraft (SFX service) ──────────────────────────────────────
echo ""
echo "Installing PyTorch 2.1.0 with CUDA 12.1 support (pinned for audiocraft compatibility)..."
pip install --quiet \
  "torch==2.1.0" \
  "torchaudio==2.1.0" \
  "torchvision==0.16.0" \
  "numpy<2" \
  --index-url https://download.pytorch.org/whl/cu121

echo "Installing audiocraft..."
pip install --quiet audiocraft

# audiocraft pulls in a recent transformers that requires torch>=2.4, but we're
# pinned to torch 2.1.0. Downgrade to the last version that works with torch 2.1.
# Without this, T5Conditioner (used by AudioGen) fails at startup with:
#   "T5EncoderModel requires the PyTorch library but it was not found"
echo "Pinning transformers to torch-2.1-compatible version..."
pip install --quiet "transformers==4.44.2"

# ── Dev / test dependencies ───────────────────────────────────────────────────
echo ""
echo "Installing test dependencies..."
pip install --quiet -r requirements-dev.txt

# ── Copy env file ─────────────────────────────────────────────────────────────
if [ ! -f .env ]; then
  cp .env.example .env
  echo ""
  echo "Created .env from .env.example — edit values if needed."
fi

echo ""
echo "Setup complete. Activate with: source .venv/bin/activate"
echo "Run unit tests now (no GPU needed): make test"
echo "To start services:                  make start"
echo "To smoke test after startup:        make wait && make smoke"
