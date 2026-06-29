# VM_SETUP.md — Running the fast renderer on a Primetime GPU VM

Step-by-step runbook to take the ShotBook streaming renderer from "code on
GitHub" to "frames actually generating fast" on a Primetime (Prime Intellect)
GPU box. Follow top to bottom; **don't skip Step 1's checks** — they catch the
common blockers before any long install.

> **GPU count: you need ONE H100 80GB. Not two.** The streaming model runs the
> DiT, VAE, and text encoder all on a single GPU. The 2-GPU split is a *later*
> optimization (deferred), and the voice demo's 2nd GPU was only for speech
> recognition, which we don't use.

How to run the commands: SSH into the VM and run them there, pasting output
back into the chat as you go. Steps marked **YOU (interactive)** need your
input (logins, instance provisioning).

---

## Part 1 — Prove the renderer is fast (no database needed)

This is the part that answers "can we generate video much quicker?". It needs
only the GPU + the renderer service — no Postgres, no ingested books.

### Step 1 — Provision + verify the box  ⬅ do this first

**1a. YOU (interactive):** start a Primetime instance:
- **1× H100 80GB**.
- An image **with the CUDA toolkit (`nvcc`)**, not just the driver — `flash-attn`
  compiles against it. A "CUDA 12.x devel" or PyTorch image is safest.
- **Disk ≥ 80GB** (~25–30GB weights + venv + repo + headroom). If you can attach
  a **persistent volume**, do it and note its mount path (e.g. `/workspace`) —
  weights go there so you don't re-download on every restart.

**1b. SSH in and run these four checks; paste all output:**
```bash
nvidia-smi --query-gpu=name,memory.total,driver_version --format=csv   # H100 + ~80GB
nvcc --version                                  # CUDA compiler — REQUIRED for flash-attn
python3 --version                               # need 3.10 or 3.11
git --version && which ffmpeg ; df -h /         # git, ffmpeg, free disk
```
Pass criteria:
- H100 with ~81559 MiB.
- `nvcc` prints a release (e.g. `release 12.4`). **If `nvcc: command not found`,
  STOP** — switch to a CUDA-devel image or install the toolkit before going on.
- Python 3.10/3.11.
- `git` present; `ffmpeg` present (if missing: `apt-get update && apt-get install -y ffmpeg`).

### Step 2 — Get the repo
```bash
# private repo -> use a GitHub PAT, or make it public, or add an SSH deploy key
git clone https://github.com/dogeyboy1932/shotbook.git
cd shotbook
```

### Step 3 — YOU (interactive): HuggingFace login (weights are gated)
```bash
pip install -U "huggingface_hub[cli]"      # if hf isn't already available
hf auth login                              # paste a HF token with access to Wan-AI/Wan2.1-T2V-1.3B
```
> First request access at https://huggingface.co/Wan-AI/Wan2.1-T2V-1.3B if prompted.

### Step 4 — Set up the renderer (venv + deps + flash-attn + weights)
If you attached a persistent volume, point weights at it (recommended):
```bash
export WEIGHTS_DIR=/workspace/shotbook-weights      # <-- your volume mount; omit to keep weights in-repo
make setup-renderer
```
What this does (≈20–40 min, mostly flash-attn build + the ~25GB download):
- creates `.venv-renderer` and installs `services/renderer/requirements.txt`
- `pip install flash-attn --no-build-isolation`  ← the slow/finicky part
- downloads `Wan-AI/Wan2.1-T2V-1.3B` + `zhuhz22/Causal-Forcing chunkwise/causal_forcing.pt`
  into `$WEIGHTS_DIR`, symlinked into `services/renderer/vendor/`

### Step 5 — Smoke test  ⬅ the real moment of truth
```bash
make smoke-renderer
```
Expected: it loads the model, writes `services/renderer/vendor/out/smoke.mp4`, and
prints something like `[smoke] 72 frames in 4.1s = 17.6 FPS`. **That FPS line is
the proof the 40-min problem is gone.** Paste the output (and, if you can, copy
the mp4 off the box: `scp` or `python -m http.server`).

### Step 6 — Run the renderer service + a direct render
Terminal A (leave running):
```bash
make start-renderer        # uvicorn on :8004, model loads warm once
```
Terminal B — hit it with a hand-written plan (no DB needed):
```bash
curl -s -X POST http://localhost:8004/render \
  -H 'Content-Type: application/json' \
  -d '{
        "shots": [
          {"shot_id":"01_test",
           "prompt":"A fluffy golden retriever sprinting through a sunlit meadow of orange wildflowers, warm golden daylight, cinematic, photorealistic, 4k"}
        ],
        "out_dir":"/tmp/rendertest",
        "seconds_per_shot":5.0
      }'
```
Returns `{"video_path": "/tmp/rendertest/final_story.mp4", "shot_count":1,
"total_frames":..., "seconds": <wall-clock>}`. If `seconds` is single/low double
digits, **Part 1 is a success** — that's a highlight-sized clip in seconds, not 40 min.

---

## Part 2 — Full end-to-end (highlight a passage → video)

Part 1 proves speed. To drive it from the reader UI you also need the backend +
a book's resolved state in Postgres.

### Step 7 — Backend + Postgres (separate venv from the renderer)
```bash
./scripts/install_vm.sh        # installs Postgres (Docker or native), .venv, applies db/schema.sql
                               # NOTE: it also installs vllm (only needed for INGESTION; heavy).
echo "ANTHROPIC_API_KEY=sk-ant-..." >> .env     # needed by /api/compose-scene (shot planning)
# tell the backend where the renderer is (default already http://localhost:8004):
# echo "BVG_RENDERER_URL=http://localhost:8004" >> .env
```

### Step 8 — Get a book's state into Postgres
The reader needs ingested data (characters/locations/paragraphs/states). Either:
- **Restore a DB dump** if you have one from the original work (fastest), or
- **Ingest a sample book** — this needs the vLLM fleet up (`./scripts/launch_vllm_cluster.sh`)
  and is the heavy path:
  ```bash
  PYTHONPATH=. python -m ingestion.orchestrator data/texts/shelley-frankenstein.txt \
      --title "Frankenstein" --author "Mary Shelley"
  ```
  > Heads-up: ingestion competes with the renderer for GPU. On a single H100,
  > ingest first, then free the GPU before serving the renderer.

### Step 9 — Run all three + open the UI
```bash
PYTHONPATH=. .venv/bin/uvicorn app.main:app --port 8080 &     # backend
make start-renderer &                                         # renderer :8004
cd frontend && npm install && npm run dev                     # :5173
```
From your laptop, tunnel just the frontend:
```bash
ssh -N -L 5173:localhost:5173 you@vm-host
```
Open `http://localhost:5173` → pick a book → highlight a passage → **Generate
Video** → poll completes → clip saved to `generated_videos/`.

---

## Troubleshooting

- **`nvcc: command not found`** → flash-attn can't build. Use a CUDA-devel image,
  or `apt-get install -y cuda-toolkit-12-4` (match your driver), or install a
  **prebuilt flash-attn wheel** matching your torch/python/CUDA instead of building.
- **flash-attn build OOM / very slow** → set `MAX_JOBS=4 pip install flash-attn --no-build-isolation`,
  or use a prebuilt wheel from the flash-attention releases page.
- **`401/403` on weight download** → `hf auth login` token lacks access; request
  access to `Wan-AI/Wan2.1-T2V-1.3B` and retry (downloads resume).
- **CUDA OOM at model load** → confirm it's an 80GB H100 and nothing else holds the
  GPU (`nvidia-smi`); the engine auto-enables slow CPU-swap below ~40GB free.
- **Renderer 503 "model not loaded"** → it's still warming up at startup; wait for
  the `renderer ready` log, then retry.
- **`final_story.mp4` not produced** → check the `start-renderer` terminal for the
  traceback; usually a missing weight file or a config path (weights must sit at
  `services/renderer/vendor/wan_models/...` and `.../checkpoints/...`).

---

## What "success" looks like
1. `make smoke-renderer` prints an FPS line (~12–22) and writes an mp4.
2. `POST /render` returns a `seconds` value in the seconds/low-tens range.
3. (Full) Highlighting a passage in the UI yields a clip in well under 2 minutes.
