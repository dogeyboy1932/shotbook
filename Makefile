.PHONY: setup test smoke e2e full start wait

setup:
	bash scripts/setup.sh

# Unit + mocked integration tests (no GPU, no network)
test:
	.venv/bin/pytest tests/test_audio_pipeline.py -v

# Curl-based smoke tests — requires all 4 services running
smoke:
	bash tests/smoke.sh

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
	bash scripts/wait_for_services.sh
