#!/usr/bin/env bash
# [1] Bring up the GPU VM: push the repo, build the env (first time), and start
# the renderer (:8004, tmux 'sb'). Blocks until the model is warm, then returns.
# Re-running on the same box is fast (rebuild is skipped if already built).
#
#   scripts/start-vm.sh            # uses VM_IP from .env
#   scripts/start-vm.sh <VM_IP>    # override for a one-off box
#
# Leave the tunnel + frontend to scripts/start-tunnel.sh and start-frontend.sh
# (run those in their own terminals).
set -euo pipefail
VM_IP="${1:-${VM_IP:-}}"
source "$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)/_config.sh"
require_vm

CM="/tmp/cm-shotbook-deploy-$VM_IP"
SSH=(ssh -o ControlMaster=auto -o "ControlPath=$CM" -o ControlPersist=15m \
        -o StrictHostKeyChecking=accept-new -o ConnectTimeout=25 \
        -p "$VM_PORT" -i "$KEYLINK")
cleanup(){ "${SSH[@]}" -O exit "$HOST" 2>/dev/null || true; }
trap cleanup EXIT

say "1/4 probing $HOST"
"${SSH[@]}" "$HOST" 'echo connected as $(whoami) on $(hostname); nvidia-smi --query-gpu=name,memory.total --format=csv,noheader'
REMOTE_HOME="$("${SSH[@]}" "$HOST" 'echo $HOME')"
RDIR="$REMOTE_HOME/shotbook"

say "2/4 pushing repo + writing VM .env (-> $RDIR)"
# Never push the laptop's root .env (frontend VITE_*, VM_* connection secrets);
# the VM gets its own curated .env written below.
rsync -az -e "ssh -p $VM_PORT -i $KEYLINK -o ControlPath=$CM" \
  --exclude _SPED --exclude .git --exclude '*.pem' --exclude '.venv*' \
  --exclude wan_models --exclude node_modules --exclude '*.mp4' \
  --exclude .env --exclude '.env.local' \
  "$REPO/" "$HOST:$RDIR/"
# Build the VM .env straight from the single root .env: carry every renderer/
# ingestion var (BVG_*) and the Anthropic key, then add the VM-derived bits.
ENVTMP="$(mktemp)"
grep -E '^(BVG_|ANTHROPIC_API_KEY=)' "$CONFIG" > "$ENVTMP" || true
{
  echo "BVG_DB_SSL_CA=$REMOTE_HOME/supabase_ca.pem"
  grep -qE '^BVG_RENDERER_URL=' "$CONFIG" || echo "BVG_RENDERER_URL=http://localhost:8004"
  grep -qE '^BVG_DB_POOL_SIZE=' "$CONFIG" || echo "BVG_DB_POOL_SIZE=5"
  grep -qE '^BVG_DB_MAX_OVERFLOW=' "$CONFIG" || echo "BVG_DB_MAX_OVERFLOW=2"
} >> "$ENVTMP"
scp -q -P "$VM_PORT" -i "$KEYLINK" -o "ControlPath=$CM" "$ENVTMP" "$HOST:$RDIR/.env"
rm -f "$ENVTMP"

say "3/4 building environment (resume_vm.sh) -- skipped if already built"
"${SSH[@]}" "$HOST" "bash -s" <<REMOTE
set -e
cd "$RDIR"
if [ -d .venv-renderer ] && [ -f renderer/vendor/checkpoints/chunkwise/causal_forcing.pt ]; then
  echo "   env already built -- skipping rebuild"
else
  tmux kill-session -t setup 2>/dev/null || true
  tmux new-session -d -s setup "bash scripts/resume_vm.sh > ~/resume.log 2>&1; echo RESUME_EXIT_\\\$? >> ~/resume.log"
  echo "   rebuild running in tmux 'setup' (apt + 3 venvs + flash-attn + ~54GB weights)"
  while ! grep -q RESUME_EXIT_ ~/resume.log 2>/dev/null; do
    sleep 20; tail -1 ~/resume.log 2>/dev/null | sed 's/^/   .../'
  done
  if ! grep -q RESUME_EXIT_0 ~/resume.log; then
    echo "!! resume_vm.sh failed:"; tail -25 ~/resume.log; exit 1
  fi
  echo "   rebuild complete"
fi
REMOTE

say "4/4 starting the renderer :8004 (plan + render -- the only VM service)"
"${SSH[@]}" "$HOST" "bash -s" <<REMOTE
set -e
cd "$RDIR"
tmux kill-session -t sb 2>/dev/null || true
# One service: the renderer owns Claude planning too, so the React app talks only
# to Supabase + this. .env is sourced for ANTHROPIC_API_KEY (planning).
tmux new-session -d -s sb -n renderer "cd $RDIR; set -a; . ./.env; set +a; CUDA_VISIBLE_DEVICES=0 .venv-renderer/bin/uvicorn renderer.main:app --host 0.0.0.0 --port 8004 > ~/renderer.log 2>&1"
printf "   waiting for the renderer to warm-load the model"
for i in \$(seq 1 90); do
  if curl -s -m5 http://localhost:8004/health 2>/dev/null | grep -q '"loaded":true'; then echo " ready"; break; fi
  printf "."; sleep 5
done
curl -s -m6 http://localhost:8004/health; echo
REMOTE

say "VM ready. Next, in two more terminals:"
echo "   scripts/start-tunnel.sh     # localhost:8004 -> VM renderer (keep open)"
echo "   scripts/start-frontend.sh   # http://localhost:5173 (keep open)"
