#!/usr/bin/env bash
# Detects the number of NVIDIA GPUs actually present on this machine right
# now, groups them into tensor-parallel replicas sized to fit the model,
# launches one vLLM OpenAI-compatible server per replica, and writes the
# resulting endpoint list to .env as BVG_VLLM_ENDPOINTS so the ingestion
# orchestrator picks it up automatically.
#
# Usage:
#   ./scripts/launch_vllm_cluster.sh [tensor_parallel_size] [base_port]
#
# Example: on a box with 2 H100s today and up to 8 on other runs, this
# script always does the right thing -- it does not assume a fixed GPU count.

set -euo pipefail

MODEL="${BVG_VLLM_MODEL_NAME:-meta-llama/Meta-Llama-3-70B-Instruct}"
TP_SIZE="${1:-2}"          # GPUs per replica; 2 is the minimum that fits a 70B fp16 model on 80GB H100s
BASE_PORT="${2:-8000}"
ENV_FILE="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)/.env"
LOG_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)/logs/vllm"

mkdir -p "$LOG_DIR"

if ! command -v nvidia-smi >/dev/null 2>&1; then
    echo "ERROR: nvidia-smi not found -- no NVIDIA GPUs detected on this host." >&2
    exit 1
fi

NUM_GPUS=$(nvidia-smi --query-gpu=index --format=csv,noheader | wc -l | tr -d ' ')
if [ "$NUM_GPUS" -lt "$TP_SIZE" ]; then
    echo "ERROR: found $NUM_GPUS GPU(s), but tensor_parallel_size=$TP_SIZE requires at least that many." >&2
    exit 1
fi

NUM_REPLICAS=$(( NUM_GPUS / TP_SIZE ))
LEFTOVER=$(( NUM_GPUS % TP_SIZE ))
if [ "$LEFTOVER" -ne 0 ]; then
    echo "NOTE: $NUM_GPUS GPUs is not evenly divisible by tensor_parallel_size=$TP_SIZE; $LEFTOVER GPU(s) will sit idle."
fi

echo "Detected $NUM_GPUS GPU(s) -> launching $NUM_REPLICAS replica(s) of $MODEL (TP=$TP_SIZE each)"

ENDPOINTS=()
PIDS=()

for ((replica = 0; replica < NUM_REPLICAS; replica++)); do
    PORT=$(( BASE_PORT + replica ))
    GPU_START=$(( replica * TP_SIZE ))
    GPU_LIST=$(seq -s, "$GPU_START" $(( GPU_START + TP_SIZE - 1 )) | sed 's/,$//')

    echo "  replica $replica: GPUs [$GPU_LIST] -> http://localhost:$PORT/v1"

    CUDA_VISIBLE_DEVICES="$GPU_LIST" \
        python -m vllm.entrypoints.openai.api_server \
            --model "$MODEL" \
            --tensor-parallel-size "$TP_SIZE" \
            --port "$PORT" \
            > "$LOG_DIR/replica_${replica}.log" 2>&1 &

    PIDS+=($!)
    ENDPOINTS+=("\"http://localhost:$PORT/v1\"")
done

echo "PIDs: ${PIDS[*]} (logs in $LOG_DIR)"
echo "Waiting for replicas to become healthy..."

for ((replica = 0; replica < NUM_REPLICAS; replica++)); do
    PORT=$(( BASE_PORT + replica ))
    until curl -sf "http://localhost:$PORT/health" >/dev/null 2>&1; do
        sleep 5
    done
    echo "  replica $replica healthy on port $PORT"
done

ENDPOINTS_JSON="[$(IFS=,; echo "${ENDPOINTS[*]}")]"

# Replace any existing BVG_VLLM_ENDPOINTS line in .env, or append one.
touch "$ENV_FILE"
if grep -q '^BVG_VLLM_ENDPOINTS=' "$ENV_FILE"; then
    sed -i.bak "s|^BVG_VLLM_ENDPOINTS=.*|BVG_VLLM_ENDPOINTS=$ENDPOINTS_JSON|" "$ENV_FILE"
else
    echo "BVG_VLLM_ENDPOINTS=$ENDPOINTS_JSON" >> "$ENV_FILE"
fi

echo "Wrote BVG_VLLM_ENDPOINTS=$ENDPOINTS_JSON to $ENV_FILE"
echo "Replica PIDs saved -- kill with: kill ${PIDS[*]}"
