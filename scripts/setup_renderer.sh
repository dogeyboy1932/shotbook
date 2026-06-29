#!/usr/bin/env bash
# Set up the renderer service on a fresh GPU VM: dedicated venv, engine deps,
# flash-attn, and the model weights. Idempotent -- safe to re-run.
#
# Prereqs (YOU run these first, interactively):
#   - a single H100 80GB instance with nvidia-smi working + CUDA toolkit (nvcc)
#   - `hf auth login`   (the Wan2.1 weights are gated on HuggingFace)
#
# Usage:
#   bash scripts/setup_renderer.sh
#
# Set WEIGHTS_DIR to a PERSISTENT volume so the ~20-30GB of weights survive VM
# restarts; they get symlinked into services/renderer/vendor/. Defaults to a
# local (non-persistent) dir if unset.
set -euo pipefail

REPO_ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
cd "$REPO_ROOT"

VENDOR="$REPO_ROOT/services/renderer/vendor"
VENV="$REPO_ROOT/.venv-renderer"
WEIGHTS_DIR="${WEIGHTS_DIR:-$VENDOR}"   # override -> persistent volume

echo "==> System deps (ffmpeg; nvcc must already be present for flash-attn)"
if command -v apt-get >/dev/null 2>&1; then
    apt-get update -qq || echo "WARN: apt-get update failed"
    apt-get install -y -qq ffmpeg ninja-build || echo "WARN: apt install failed (install ffmpeg/ninja-build manually)"
fi

echo "==> Creating renderer venv at $VENV"
python3 -m venv "$VENV"
# shellcheck disable=SC1091
source "$VENV/bin/activate"
pip install -q --upgrade pip
pip install -q -r "$REPO_ROOT/services/renderer/requirements.txt"

echo "==> Installing flash-attn (no build isolation; needs nvcc + ninja; ~10-20 min)"
pip install -q flash-attn --no-build-isolation || {
    echo "ERROR: flash-attn build failed. Common fixes:"
    echo "  - ensure CUDA toolkit (nvcc) matches your torch CUDA build"
    echo "  - or install a prebuilt wheel matching your torch/python/CUDA"
    exit 1
}

echo "==> Downloading weights into $WEIGHTS_DIR (skips files already present)"
mkdir -p "$WEIGHTS_DIR/wan_models" "$WEIGHTS_DIR/checkpoints"
# Base model: umT5-xxl text encoder (~11GB) + Wan VAE + DiT config/weights.
hf download Wan-AI/Wan2.1-T2V-1.3B --local-dir "$WEIGHTS_DIR/wan_models/Wan2.1-T2V-1.3B"
# Distilled streaming checkpoint -- default (chunkwise 4-step, gino's live model).
hf download zhuhz22/Causal-Forcing chunkwise/causal_forcing.pt --local-dir "$WEIGHTS_DIR/checkpoints"
# Optional faster variant (frame-wise 2-step); uncomment to also fetch:
# hf download zhuhz22/Causal-Forcing causal-forcing++/framewise-2step.pt --local-dir "$WEIGHTS_DIR/checkpoints"

# If weights live on a persistent volume outside the repo, symlink them in so the
# engine's relative paths (wan_models/..., checkpoints/...) resolve from VENDOR.
if [ "$WEIGHTS_DIR" != "$VENDOR" ]; then
    ln -sfn "$WEIGHTS_DIR/wan_models" "$VENDOR/wan_models"
    ln -sfn "$WEIGHTS_DIR/checkpoints" "$VENDOR/checkpoints"
fi

echo "==> Done."
echo ""
echo "Smoke test (Phase 0 -- prove frames generate):"
echo "  CUDA_VISIBLE_DEVICES=0 $VENV/bin/python services/renderer/vendor/cf_streaming.py --seconds 5"
echo ""
echo "Start the renderer service:"
echo "  CUDA_VISIBLE_DEVICES=0 $VENV/bin/uvicorn services.renderer.main:app --host 0.0.0.0 --port 8004"
