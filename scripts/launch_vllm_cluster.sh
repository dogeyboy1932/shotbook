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
# Set GPU_DEVICES to a comma-separated list of GPU IDs to restrict which
# GPUs are used (e.g. GPU_DEVICES=4,5,6,7 when GPUs 0-3 are reserved for Wan).
# When unset, all GPUs detected by nvidia-smi are used.
#
# Default model is the 4-bit AWQ Qwen-72B (~42 GB), which fits on ONE 80GB
# H100 -- so the default TP_SIZE is 1 (one replica per GPU, no NCCL).
#
# Example (4 GPUs reserved for Wan, 4 for LLM, one replica per GPU):
#   GPU_DEVICES=4,5,6,7 ./scripts/launch_vllm_cluster.sh

set -euo pipefail

REPO_ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
PYTHON="${REPO_ROOT}/.venv/bin/python"
if [ ! -x "$PYTHON" ]; then
    PYTHON="$(which python3)"
fi

# vLLM 0.23+ ships its own libcudart.so.13 inside the venv's nvidia/cu13 package.
# Add it to LD_LIBRARY_PATH so the dynamic linker can find it at startup.
VENV_CUDA13="${REPO_ROOT}/.venv/lib/python3.10/site-packages/nvidia/cu13/lib"
if [ -d "$VENV_CUDA13" ]; then
    export LD_LIBRARY_PATH="${VENV_CUDA13}${LD_LIBRARY_PATH:+:${LD_LIBRARY_PATH}}"
fi

# Default to the AWQ (4-bit) model -- it fits on a single GPU.
MODEL="${BVG_VLLM_MODEL_NAME:-Qwen/Qwen2.5-32B-Instruct}"
TP_SIZE="${1:-1}"          # 1 = one GPU per replica, no tensor parallelism, no NCCL
BASE_PORT="${2:-8000}"
ENV_FILE="${REPO_ROOT}/.env"
LOG_DIR="${REPO_ROOT}/logs/vllm"

# If the model name contains AWQ, tell vLLM to load it as AWQ-quantized.
# (Without this flag vLLM reads the 4-bit weights as full precision and crashes.)
QUANT_ARGS=()
if [[ "$MODEL" == *AWQ* || "$MODEL" == *awq* ]]; then
    QUANT_ARGS=(--quantization awq)
fi

mkdir -p "$LOG_DIR"

if ! command -v nvidia-smi >/dev/null 2>&1; then
    echo "ERROR: nvidia-smi not found -- no NVIDIA GPUs detected on this host." >&2
    exit 1
fi

# Build the GPU array: explicit list from GPU_DEVICES, or all GPUs from nvidia-smi.
if [ -n "${GPU_DEVICES:-}" ]; then
    IFS=',' read -ra GPU_ARRAY <<< "$GPU_DEVICES"
    NUM_GPUS=${#GPU_ARRAY[@]}
    echo "Using GPU_DEVICES=$GPU_DEVICES ($NUM_GPUS GPU(s))"
else
    NUM_GPUS=$(nvidia-smi --query-gpu=index --format=csv,noheader | wc -l | tr -d ' ')
    GPU_ARRAY=()
    for ((i=0; i<NUM_GPUS; i++)); do GPU_ARRAY+=("$i"); done
fi

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
if [ "${#QUANT_ARGS[@]}" -gt 0 ]; then
    echo "Quantization: awq"
fi

ENDPOINTS=()
PIDS=()

for ((replica = 0; replica < NUM_REPLICAS; replica++)); do
    PORT=$(( BASE_PORT + replica ))

    # Pick TP_SIZE consecutive GPUs from the allowed set for this replica.
    GPU_LIST=""
    for ((g=0; g<TP_SIZE; g++)); do
        idx=$(( replica * TP_SIZE + g ))
        [ -n "$GPU_LIST" ] && GPU_LIST="${GPU_LIST},"
        GPU_LIST="${GPU_LIST}${GPU_ARRAY[$idx]}"
    done

    echo "  replica $replica: GPUs [$GPU_LIST] -> http://localhost:$PORT/v1"
    
    CUDA_VISIBLE_DEVICES="$GPU_LIST" \
        "$PYTHON" -m vllm.entrypoints.openai.api_server \
            --model "$MODEL" \
            --tensor-parallel-size "$TP_SIZE" \
            "${QUANT_ARGS[@]}" \
            --enforce-eager \
            --max-model-len 16384 \
            --port "$PORT" \
            --disable-custom-all-reduce \
            > "$LOG_DIR/replica_${replica}.log" 2>&1 &

    PIDS+=($!)
    ENDPOINTS+=("\"http://localhost:$PORT/v1\"")
done

echo "PIDs: ${PIDS[*]} (logs in $LOG_DIR)"
echo "Waiting for replicas to become healthy..."

# Health wait that ALSO bails if a replica process has died -- so a startup
# crash fails fast with the log path instead of hanging forever on a corpse.
for ((replica = 0; replica < NUM_REPLICAS; replica++)); do
    PORT=$(( BASE_PORT + replica ))
    PID=${PIDS[$replica]}
    until curl -sf "http://localhost:$PORT/health" >/dev/null 2>&1; do
        if ! kill -0 "$PID" 2>/dev/null; then
            echo "ERROR: replica $replica (pid $PID) died before becoming healthy." >&2
            echo "       Last lines of its log:" >&2
            tail -n 30 "$LOG_DIR/replica_${replica}.log" >&2
            echo "ERROR: aborting. Full log: $LOG_DIR/replica_${replica}.log" >&2
            # clean up any siblings still running
            for p in "${PIDS[@]}"; do kill "$p" 2>/dev/null || true; done
            exit 1
        fi
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