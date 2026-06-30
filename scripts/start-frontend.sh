#!/usr/bin/env bash
# [3] Start the Vite dev server in the foreground (http://localhost:5173). Reads
# VITE_* straight from the repo-root .env (vite.config.ts envDir: '..'). Keep this
# terminal open while you use the app; Ctrl-C stops it.
#
#   scripts/start-frontend.sh
set -euo pipefail
HERE="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
REPO="$(cd "$HERE/.." && pwd)"

cd "$REPO/frontend"
[ -d node_modules ] || { echo "==> installing frontend deps (first run)"; npm install; }
echo "==> Vite on http://localhost:5173  (Ctrl-C to stop)"
exec npm run dev
