#!/usr/bin/env bash
# One-command ShotBook bring-up on a FRESH GPU instance.
#
# Run from your laptop, from the repo root:
#     scripts/deploy.sh <VM_IP>
#
# Everything else (SSH key path, DB URL, Anthropic key) lives ONCE in
# scripts/deploy.config -- copy scripts/deploy.config.example to that path and
# fill it in. The only per-instance argument is the IP, because that's the only
# thing that changes when you spin up a new box.
#
# What it does, end to end, blocking until the box is usable:
#   1. probe the VM (SSH + GPU)
#   2. rsync the repo up (no weights/venvs/secrets) and write the VM .env
#   3. run scripts/resume_vm.sh (venvs + flash-attn + weights) -- skipped if
#      the env is already built, so re-running on the same box is fast
#   4. start backend :8080 + renderer :8004 in tmux 'sb' and wait until warm
#   5. open the local SSH tunnel and start the Vite frontend
#   6. print the URL -- highlight -> video is live
set -euo pipefail

HERE="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
REPO="$(cd "$HERE/.." && pwd)"
CONFIG="${DEPLOY_CONFIG:-$HERE/deploy.config}"

VM_IP="${1:-}"
if [ -z "$VM_IP" ]; then echo "usage: scripts/deploy.sh <VM_IP>"; exit 1; fi
if [ ! -f "$CONFIG" ]; then
  echo "!! missing $CONFIG"
  echo "   cp scripts/deploy.config.example scripts/deploy.config  and fill it in"
  exit 1
fi

# --- read config (grep, not source: tolerates spaces/special chars in values) ---
cfg(){ grep -E "^$1=" "$CONFIG" | head -1 | cut -d= -f2- | sed -e 's/^"//' -e 's/"$//'; }
KEY="$(cfg VM_SSH_KEY)"
VM_USER="$(cfg VM_USER)";  VM_USER="${VM_USER:-root}"
VM_PORT="$(cfg VM_PORT)";  VM_PORT="${VM_PORT:-22}"
DBURL="$(cfg BVG_DATABASE_URL)"
ANTHRO="$(cfg ANTHROPIC_API_KEY)"
LOCAL_PORT="$(cfg LOCAL_BACKEND_PORT)"; LOCAL_PORT="${LOCAL_PORT:-8080}"

[ -f "$KEY" ]   || { echo "!! SSH key not found: $KEY"; exit 1; }
[ -n "$DBURL" ] || { echo "!! BVG_DATABASE_URL not set in $CONFIG"; exit 1; }
[ -n "$ANTHRO" ]|| { echo "!! ANTHROPIC_API_KEY not set in $CONFIG"; exit 1; }

HOST="$VM_USER@$VM_IP"
# The key path may contain spaces (repo dir does). rsync -e can't handle that,
# so symlink it to a space-free path and use that everywhere.
KEYLINK="/tmp/.shotbook-deploykey-$VM_IP"
ln -sfn "$KEY" "$KEYLINK"
CM="/tmp/cm-shotbook-deploy-$VM_IP"
SSH=(ssh -o ControlMaster=auto -o "ControlPath=$CM" -o ControlPersist=15m \
        -o StrictHostKeyChecking=accept-new -o ConnectTimeout=25 \
        -p "$VM_PORT" -i "$KEYLINK")
cleanup(){ "${SSH[@]}" -O exit "$HOST" 2>/dev/null || true; rm -f "$KEYLINK"; }
trap cleanup EXIT

say(){ printf '\n\033[1;36m==> %s\033[0m\n' "$*"; }

say "1/6 probing $HOST"
"${SSH[@]}" "$HOST" 'echo connected as $(whoami) on $(hostname); nvidia-smi --query-gpu=name,memory.total --format=csv,noheader'
REMOTE_HOME="$("${SSH[@]}" "$HOST" 'echo $HOME')"
RDIR="$REMOTE_HOME/shotbook"

say "2/6 pushing repo + writing VM .env (-> $RDIR)"
rsync -az -e "ssh -p $VM_PORT -i $KEYLINK -o ControlPath=$CM" \
  --exclude _SPED --exclude .git --exclude '*.pem' --exclude '.venv*' \
  --exclude wan_models --exclude node_modules --exclude '*.mp4' \
  --exclude supabase.txt --exclude scripts/deploy.config \
  "$REPO/" "$HOST:$RDIR/"
# Build the VM .env from config; CA path is derived from the remote home.
ENVTMP="$(mktemp)"
{
  echo "BVG_DATABASE_URL=$DBURL"
  echo "ANTHROPIC_API_KEY=$ANTHRO"
  echo "BVG_DB_SSL_CA=$REMOTE_HOME/supabase_ca.pem"
  echo "BVG_RENDERER_URL=http://localhost:8004"
  echo "BVG_DB_POOL_SIZE=5"
  echo "BVG_DB_MAX_OVERFLOW=2"
} > "$ENVTMP"
scp -q -P "$VM_PORT" -i "$KEYLINK" -o "ControlPath=$CM" "$ENVTMP" "$HOST:$RDIR/.env"
rm -f "$ENVTMP"

say "3/6 building environment (resume_vm.sh) -- skipped if already built"
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

say "4/6 starting the renderer :8004 (plan + render — the only VM service)"
"${SSH[@]}" "$HOST" "bash -s" <<REMOTE
set -e
cd "$RDIR"
tmux kill-session -t sb 2>/dev/null || true
# One service: the renderer now owns Claude planning too, so the React app talks
# only to Supabase + this. .env is sourced for ANTHROPIC_API_KEY (planning).
tmux new-session -d -s sb -n renderer "cd $RDIR; set -a; . ./.env; set +a; CUDA_VISIBLE_DEVICES=0 .venv-renderer/bin/uvicorn renderer.main:app --host 0.0.0.0 --port 8004 > ~/renderer.log 2>&1"
printf "   waiting for the renderer to warm-load the model"
for i in \$(seq 1 90); do
  if curl -s -m5 http://localhost:8004/health 2>/dev/null | grep -q '"loaded":true'; then echo " ready"; break; fi
  printf "."; sleep 5
done
curl -s -m6 http://localhost:8004/health; echo
REMOTE

say "5/6 opening local tunnel (VM :8004) + frontend"
# The browser calls the VM renderer directly at localhost:8004 (VITE_VM_BASE_URL),
# so tunnel that port. Book/state data goes straight to Supabase (no tunnel).
pkill -f "8004:localhost:8004" 2>/dev/null || true
ssh -fN -o ServerAliveInterval=30 -o ExitOnForwardFailure=yes \
    -L "8004:localhost:8004" -p "$VM_PORT" -i "$KEYLINK" "$HOST"
echo "   tunnel up: localhost:8004 -> VM renderer"
if [ -d "$REPO/frontend/node_modules" ]; then
  ( cd "$REPO/frontend" && nohup npm run dev > /tmp/shotbook-vite.log 2>&1 & )
  echo "   frontend starting (log: /tmp/shotbook-vite.log)"
else
  echo "   frontend deps missing -- run: cd frontend && npm install && npm run dev"
fi

say "6/6 READY"
cat <<DONE

  Open:  http://localhost:5173
  (frontend/.env.local needs VITE_SUPABASE_URL, VITE_SUPABASE_ANON_KEY,
   VITE_VM_BASE_URL=http://localhost:8004)

  VM service:   tmux 'sb' on $HOST  (renderer :8004 — plan + render)
  Stop laptop side later:
     pkill -f "8004:localhost:8004"   # tunnel
     pkill -f vite                    # frontend
  Stop GPU spend: terminate the instance.
DONE
