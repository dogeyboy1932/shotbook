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
sudo apt-get update -qq
sudo apt-get install -y -qq python3-dev python3-venv docker.io postgresql-client

echo "==> Starting Postgres (Docker)"
if ! sudo docker ps --format '{{.Names}}' | grep -q '^bvg_pg$'; then
    if sudo docker ps -a --format '{{.Names}}' | grep -q '^bvg_pg$'; then
        sudo docker start bvg_pg
    else
        sudo docker run -d --name bvg_pg -e POSTGRES_PASSWORD=postgres -p 5432:5432 postgres:16
    fi
fi
echo "    waiting for Postgres to accept connections..."
until sudo docker exec bvg_pg pg_isready -U postgres >/dev/null 2>&1; do
    sleep 1
done
sudo docker exec -i bvg_pg psql -U postgres -d postgres < db/schema.sql

echo "==> Writing .env (only if missing -- won't clobber an existing config)"
if [ ! -f .env ]; then
    cat > .env <<'EOF'
BVG_DATABASE_URL=postgresql+asyncpg://postgres:postgres@localhost:5432/postgres
BVG_VLLM_MODEL_NAME=meta-llama/Meta-Llama-3-70B-Instruct
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
echo "Next: export BVG_VLLM_MODEL_NAME=<model>, then ./scripts/launch_vllm_cluster.sh"
