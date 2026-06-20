#!/usr/bin/env bash
# Poll all 4 /health endpoints until they respond or timeout.
# The SFX service loads AudioGen on startup which takes ~30-60s on first run.
set -euo pipefail

TIMEOUT=${WAIT_TIMEOUT:-180}
INTERVAL=3

declare -A SERVICES=(
  ["Director"]="http://localhost:8000/health"
  ["TTS"]="http://localhost:8001/health"
  ["SFX"]="http://localhost:8002/health"
  ["Mixer"]="http://localhost:8003/health"
)

echo "Waiting for services (timeout: ${TIMEOUT}s)..."
echo "Note: SFX loads AudioGen on startup — may take 30-60s on first run."
echo ""

for name in "${!SERVICES[@]}"; do
  url="${SERVICES[$name]}"
  elapsed=0
  printf "%-12s" "$name"
  until curl -sf "$url" >/dev/null 2>&1; do
    if [[ $elapsed -ge $TIMEOUT ]]; then
      echo " TIMEOUT after ${TIMEOUT}s"
      echo "Check logs — service may have failed to start."
      exit 1
    fi
    sleep $INTERVAL
    elapsed=$((elapsed + INTERVAL))
    printf "."
  done
  response=$(curl -sf "$url")
  echo " ready  ($response)"
done

echo ""
echo "All services ready."
