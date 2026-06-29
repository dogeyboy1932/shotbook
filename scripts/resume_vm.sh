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
sudo apt-get update -qq && sudo apt-get install -y -qq python3.10-venv ffmpeg openssl

echo "==> Supabase private CA (extract from the pooler; their docs' verify-ca method)"
openssl s_client -starttls postgres -connect aws-1-us-east-1.pooler.supabase.com:5432 -showcerts \
  </dev/null 2>/dev/null | awk '/-----BEGIN CERTIFICATE-----/,/-----END CERTIFICATE-----/' \
  > "$HOME/supabase_ca.pem"
echo "    wrote $HOME/supabase_ca.pem ($(grep -c 'BEGIN CERT' "$HOME/supabase_ca.pem") certs)"

PT="--index-url https://download.pytorch.org/whl/cu121"

echo "==> [1/3] backend venv (.venv-api)"
python3 -m venv .venv-api && . .venv-api/bin/activate && pip install -q -U pip
pip install -q "fastapi>=0.111" "uvicorn[standard]>=0.29" "sqlalchemy[asyncio]>=2.0" \
  asyncpg "pydantic>=2" pydantic-settings anthropic httpx
deactivate

echo "==> [2/3] fast 1.3B streaming renderer venv (.venv-renderer) + flash-attn wheel + weights"
python3 -m venv .venv-renderer && . .venv-renderer/bin/activate && pip install -q -U pip
pip install -q torch==2.5.1 torchvision==0.20.1 $PT
pip install -q -r services/renderer/requirements.txt
ABI=$(python -c 'import torch;print("TRUE" if torch._C._GLIBCXX_USE_CXX11_ABI else "FALSE")')
pip install -q "https://github.com/Dao-AILab/flash-attention/releases/download/v2.7.4.post1/flash_attn-2.7.4.post1+cu12torch2.5cxx11abi${ABI}-cp310-cp310-linux_x86_64.whl"
python - <<'PY'
from huggingface_hub import snapshot_download, hf_hub_download
snapshot_download("Wan-AI/Wan2.1-T2V-1.3B",
                  local_dir="services/renderer/vendor/wan_models/Wan2.1-T2V-1.3B", max_workers=8)
hf_hub_download("zhuhz22/Causal-Forcing", "chunkwise/causal_forcing.pt",
                local_dir="services/renderer/vendor/checkpoints")
PY
deactivate

echo "==> [3/3] quality 5B venv (.venv-quality, newer diffusers) + weights"
python3 -m venv .venv-quality && . .venv-quality/bin/activate && pip install -q -U pip
pip install -q torch==2.5.1 torchvision==0.20.1 $PT
pip install -q -U diffusers transformers accelerate ftfy imageio imageio-ffmpeg "numpy<2" safetensors sentencepiece
python - <<'PY'
from huggingface_hub import snapshot_download
snapshot_download("Wan-AI/Wan2.2-TI2V-5B-Diffusers",
                  local_dir="wan_models/Wan2.2-TI2V-5B-Diffusers", max_workers=8)
PY
deactivate

echo ""
echo "==> RESUME COMPLETE. Make sure ~/shotbook/.env exists with:"
echo "      BVG_DATABASE_URL=postgresql+asyncpg://postgres.<ref>:<pw>@aws-1-us-east-1.pooler.supabase.com:5432/postgres"
echo "      BVG_DB_SSL_CA=$HOME/supabase_ca.pem"
echo "      ANTHROPIC_API_KEY=sk-ant-..."
echo "  Then start services in tmux:"
echo "      tmux new -s sb"
echo "      PYTHONPATH=. .venv-api/bin/uvicorn app.main:app --host 0.0.0.0 --port 8080"
echo "      CUDA_VISIBLE_DEVICES=0 .venv-renderer/bin/uvicorn services.renderer.main:app --host 0.0.0.0 --port 8004"
