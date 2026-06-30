# Shared config for the ShotBook start scripts. SOURCE this; don't run it.
# Everything is read from the single repo-root .env (copy .env.example -> .env).
#
#   VM_IP, VM_USER, VM_PORT, VM_SSH_KEY  -- the GPU box
#   BVG_DATABASE_URL, ANTHROPIC_API_KEY  -- renderer/ingestion (written to the VM .env)
#   VITE_*                               -- frontend (read by Vite via envDir)

HERE="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
REPO="$(cd "$HERE/.." && pwd)"
CONFIG="${DEPLOY_CONFIG:-$REPO/.env}"
[ -f "$CONFIG" ] || { echo "!! missing $CONFIG -- cp .env.example .env and fill it in"; exit 1; }

# grep (not source) so values with spaces/special chars are safe.
cfg(){ grep -E "^$1=" "$CONFIG" | head -1 | cut -d= -f2- | sed -e 's/^"//' -e 's/"$//'; }

# VM_IP comes from .env; a value already in the environment (e.g. a CLI arg the
# caller assigned) overrides it for a one-off box.
VM_IP="${VM_IP:-$(cfg VM_IP)}"
VM_USER="$(cfg VM_USER)"; VM_USER="${VM_USER:-root}"
VM_PORT="$(cfg VM_PORT)"; VM_PORT="${VM_PORT:-22}"
VM_KEY="$(cfg VM_SSH_KEY)"
HOST="$VM_USER@$VM_IP"

say(){ printf '\n\033[1;36m==> %s\033[0m\n' "$*"; }

# Validate the VM connection and prepare a space-free key symlink (rsync/ssh -e
# can't handle spaces in the key path; the repo dir has one). Sets KEYLINK.
require_vm(){
  [ -n "$VM_IP" ] || { echo "!! VM_IP not set -- add VM_IP=<ip> to .env (or: VM_IP=<ip> $0)"; exit 1; }
  [ -f "$VM_KEY" ] || { echo "!! SSH key not found: $VM_KEY (set VM_SSH_KEY in .env)"; exit 1; }
  KEYLINK="/tmp/.shotbook-key-$VM_IP"; ln -sfn "$VM_KEY" "$KEYLINK"
}
