#!/usr/bin/env bash
# Start one of the two external inference servers.
# Run each in its own tmux pane from the repo root.
#
# Usage:
#   bash scripts/start_inference.sh vllm   # Director LLM, GPU 0, port 30000
#   bash scripts/start_inference.sh tts    # Fish Speech TTS, GPU 1, port 30001
set -euo pipefail

MODE="${1:-}"

case "$MODE" in
  vllm)
    echo "Starting vLLM (Llama-3.1-8B-Instruct) on GPU 0 → port 30000"
    CUDA_VISIBLE_DEVICES=0 /tmp/vllm-venv/bin/vllm serve \
      meta-llama/Meta-Llama-3.1-8B-Instruct \
      --port 30000 \
      --host 0.0.0.0 \
      --gpu-memory-utilization 0.9
    ;;
  tts)
    echo "Starting Fish Speech TTS on GPU 1 → port 30001"
    cd /tmp/fish-speech
    CUDA_VISIBLE_DEVICES=1 /tmp/fish-venv/bin/python tools/api_server.py \
      --llama-checkpoint-path checkpoints/fish-speech-1.5 \
      --decoder-checkpoint-path checkpoints/fish-speech-1.5/firefly-gan-vq-fsq-8x1024-21hz-generator.pth \
      --device cuda \
      --listen 0.0.0.0:30001
    ;;
  *)
    echo "Usage: $0 {vllm|tts}"
    echo ""
    echo "  vllm  Start vLLM LLM server  (Director backend, GPU 0, port 30000)"
    echo "  tts   Start Fish Speech server (TTS backend,     GPU 1, port 30001)"
    echo ""
    echo "Run setup_inference.sh first if either venv is missing."
    exit 1
    ;;
esac
