#!/usr/bin/env bash
# One-shot setup for a fresh GPU VM (Ubuntu, NVIDIA GPUs, nvidia-smi already
# working). Brings the box from "freshly provisioned" to "ready to run
# ./scripts/launch_vllm_cluster.sh and ingest a book" -- Postgres, the Python
# venv, and vLLM's native deps.
#
# Usage:
#   ./scripts/install_vm.sh
#
# Idempotent: safe to re-run if a step fails partway through.
set -euo pipefail

REPO_ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
cd "$REPO_ROOT"

echo "==> Installing OS packages"
# python3-dev (Python.h) is easy to miss: vLLM JIT-compiles CUDA kernels at
# the first real request and needs it to do so. Without it, replicas load
# the model and pass the health check, then fail on the first request with
# a "Python.h: No such file or directory" buried deep in the engine-core
# traceback. Install it up front. Package name varies by Python version
# (python3.10-dev on 22.04, python3-dev tracks default python3 on 24.04+).
apt-get update -qq
apt-get install -y -qq python3-dev python3-venv postgresql postgresql-client

echo "==> Starting Postgres (native)"
# Start the cluster if it isn't already running.
if ! pg_isready -q 2>/dev/null; then
    pg_ctlcluster 14 main start || service postgresql start
    until pg_isready -q; do sleep 1; done
fi

# Set the postgres user password so asyncpg can authenticate over TCP.
su -c "psql -c \"ALTER USER postgres PASSWORD 'postgres';\"" postgres

# Create the app database if it doesn't exist, then apply the schema.
su -c "psql -tc \"SELECT 1 FROM pg_database WHERE datname='book_video_gen'\" | grep -q 1 || createdb book_video_gen" postgres
su -c "psql -d book_video_gen" postgres < db/schema.sql

echo "==> Writing .env (only if missing -- won't clobber an existing config)"
if [ ! -f .env ]; then
    cat > .env <<'EOF'
BVG_DATABASE_URL=postgresql+asyncpg://postgres:postgres@localhost:5432/book_video_gen
BVG_VLLM_MODEL_NAME=Qwen/Qwen2.5-72B-Instruct
EOF
fi

echo "==> Creating venv and installing Python deps"
python3 -m venv .venv
source .venv/bin/activate
pip install -q --upgrade pip
pip install -q -r requirements.txt
# Not in requirements.txt -- heavy CUDA-specific wheel, installed separately
# so a plain API/DB-only setup doesn't pay its cost.
pip install -q vllm

echo "==> Done."
echo "Next: huggingface-cli download Qwen/Qwen2.5-72B-Instruct, then GPU_DEVICES=4,5,6,7 ./scripts/launch_vllm_cluster.sh"
