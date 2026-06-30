#!/usr/bin/env bash
# Rebuild the FULL ShotBook GPU environment on a fresh instance, exactly as it
# was set up (encodes everything we learned the hard way). Idempotent-ish.
#
# Prereqs on the box: Ubuntu 22.04 + an H100 (nvidia driver working), ~80GB disk.
# NO CUDA toolkit / nvcc needed -- flash-attn is installed as a prebuilt wheel.
#
# Resume flow on a NEW instance:
#   1) provision H100 + get its key/IP
#   2) from your laptop:  rsync the repo up (excludes _SPED/.git/*.pem/weights), and the .env
#        rsync -az -e "ssh -i <newkey>" --exclude _SPED --exclude .git --exclude '*.pem' \
#          --exclude '.venv*' --exclude wan_models --exclude node_modules \
#          ./ ubuntu@<newip>:~/shotbook/
#        scp -i <newkey> /path/to/vm.env ubuntu@<newip>:~/shotbook/.env
#   3) ssh in:  cd ~/shotbook && bash scripts/resume_vm.sh
#   4) start services in tmux (see bottom of this script's output)
set -e
cd "$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"   # repo root
echo "==> OS deps"
SUDO=$(command -v sudo >/dev/null 2>&1 && echo sudo || echo "")
$SUDO apt-get update -qq && $SUDO apt-get install -y -qq python3.10-venv ffmpeg openssl

echo "==> Supabase private CA (extract from the pooler; their docs' verify-ca method)"
openssl s_client -starttls postgres -connect aws-1-us-east-1.pooler.supabase.com:5432 -showcerts \
  </dev/null 2>/dev/null | awk '/-----BEGIN CERTIFICATE-----/,/-----END CERTIFICATE-----/' \
  > "$HOME/supabase_ca.pem"
echo "    wrote $HOME/supabase_ca.pem ($(grep -c 'BEGIN CERT' "$HOME/supabase_ca.pem") certs)"

PT="--index-url https://download.pytorch.org/whl/cu121"

echo "==> [1/2] ingestion venv (.venv-api) -- Claude ingestion -> Supabase (no FastAPI)"
python3 -m venv .venv-api && . .venv-api/bin/activate && pip install -q -U pip
pip install -q "sqlalchemy[asyncio]>=2.0" asyncpg "pydantic>=2" pydantic-settings anthropic tqdm
deactivate

echo "==> [2/2] streaming renderer venv (.venv-renderer) + flash-attn wheel + weights"
echo "    (also installs anthropic + pydantic-settings for on-VM Claude shot planning)"
python3 -m venv .venv-renderer && . .venv-renderer/bin/activate && pip install -q -U pip
pip install -q torch==2.5.1 torchvision==0.20.1 $PT
pip install -q -r renderer/requirements.txt
ABI=$(python -c 'import torch;print("TRUE" if torch._C._GLIBCXX_USE_CXX11_ABI else "FALSE")')
pip install -q "https://github.com/Dao-AILab/flash-attention/releases/download/v2.7.4.post1/flash_attn-2.7.4.post1+cu12torch2.5cxx11abi${ABI}-cp310-cp310-linux_x86_64.whl"
python - <<'PY'
from huggingface_hub import snapshot_download, hf_hub_download
snapshot_download("Wan-AI/Wan2.1-T2V-1.3B",
                  local_dir="renderer/vendor/wan_models/Wan2.1-T2V-1.3B", max_workers=8)
hf_hub_download("zhuhz22/Causal-Forcing", "chunkwise/causal_forcing.pt",
                local_dir="renderer/vendor/checkpoints")
PY
deactivate

echo ""
echo "==> RESUME COMPLETE. Make sure ~/shotbook/.env exists with:"
echo "      BVG_DATABASE_URL=postgresql+asyncpg://postgres.<ref>:<pw>@aws-1-us-east-1.pooler.supabase.com:5432/postgres"
echo "      BVG_DB_SSL_CA=$HOME/supabase_ca.pem"
echo "      ANTHROPIC_API_KEY=sk-ant-...   (used for shot planning AND ingestion)"
echo "  Start the single VM service in tmux (renderer = plan + render, port 8004):"
echo "      tmux new -s sb"
echo "      set -a; . ./.env; set +a"
echo "      CUDA_VISIBLE_DEVICES=0 .venv-renderer/bin/uvicorn renderer.main:app --host 0.0.0.0 --port 8004"
echo "  Ingest a new story (writes to Supabase):"
echo "      set -a; . ./.env; set +a"
echo "      PYTHONPATH=. .venv-api/bin/python -m ingestion.orchestrator example_corpus/<book>.txt --title '...' --author '...'"
