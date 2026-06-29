.PHONY: setup setup-inference start-vllm start-tts test smoke e2e full start wait setup-renderer start-renderer smoke-renderer

# Services venv (.venv) — FastAPI services only, no GPU needed
setup:
	bash scripts/setup.sh

# External inference servers — vLLM + Fish Speech (run once per cluster)
setup-inference:
	bash scripts/setup_inference.sh

# Start individual inference servers (each in its own tmux pane)
start-vllm:
	bash scripts/start_inference.sh vllm

start-tts:
	bash scripts/start_inference.sh tts

# Unit + mocked integration tests (no GPU, no network)
test:
	.venv/bin/pytest tests/test_audio_pipeline.py -v

# Curl-based smoke tests — requires all 4 services running
smoke:
	@[ -f .env ] && export $$(cat .env | grep -v '^#' | xargs); bash tests/smoke.sh

# WebSocket TTFAB latency test — requires all 4 services + inference servers
e2e:
	.venv/bin/python tests/e2e_websocket.py

# Full flow: Director → ScriptBlock → Mixer → MP3
full:
	.venv/bin/python tests/e2e_full_pipeline.py

# Start all 4 FastAPI services (reads .env if present)
start:
	@[ -f .env ] && export $$(cat .env | grep -v '^#' | xargs); bash services/launcher.sh

# Block until all 4 /health endpoints respond (use before smoke/e2e)
wait:
	@[ -f .env ] && export $$(cat .env | grep -v '^#' | xargs); bash scripts/wait_for_services.sh

# --- Fast video renderer (services/renderer, OWN venv: .venv-renderer, GPU) ---

# One-time VM setup: renderer venv + engine deps + flash-attn + model weights.
# Run `hf auth login` first (Wan2.1 weights are gated).
setup-renderer:
	bash scripts/setup_renderer.sh

# Phase 0 smoke: prove the streaming engine generates frames (writes an mp4 + prints FPS)
smoke-renderer:
	CUDA_VISIBLE_DEVICES=0 .venv-renderer/bin/python services/renderer/vendor/cf_streaming.py --seconds 5

# Start the warm renderer service on :8004 (GPU 0)
start-renderer:
	CUDA_VISIBLE_DEVICES=0 .venv-renderer/bin/uvicorn services.renderer.main:app --host 0.0.0.0 --port 8004
