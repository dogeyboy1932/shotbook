#!/usr/bin/env bash
# [2] Open the SSH tunnel localhost:8004 -> VM renderer and HOLD it in the
# foreground (this terminal owns the tunnel; Ctrl-C closes it). The browser calls
# the renderer directly at localhost:8004 (VITE_VM_BASE_URL); book/state data goes
# straight to Supabase, so only this one port is tunneled.
#
#   scripts/start-tunnel.sh            # uses VM_IP from .env
#   scripts/start-tunnel.sh <VM_IP>
set -euo pipefail
VM_IP="${1:-${VM_IP:-}}"
source "$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)/_config.sh"
require_vm

say "tunnel: localhost:8004 -> $HOST:8004  (Ctrl-C to stop)"
exec ssh -N -o ServerAliveInterval=30 -o ExitOnForwardFailure=yes \
    -L "8004:localhost:8004" -p "$VM_PORT" -i "$KEYLINK" "$HOST"
