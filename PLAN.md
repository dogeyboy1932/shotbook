# PLAN.md — Real-Time Video Generation for ShotBook

High-level view of the work to replace the slow video renderer with a fast,
interactive one. For the full design rationale see the approved plan; this file
is the at-a-glance map.

## The problem (one line)
ShotBook's data/context pipeline is solid, but the renderer (`generate_video.py`,
Wan2.2 **14B**) takes **~40 min per clip** — that kills interactivity.

## The fix (one line)
Swap the 14B batch model for the **autoregressive streaming 1.3B model** from
`_SPED/` (Causal Forcing on Wan2.1-T2V-1.3B), which renders **~16–22 FPS on one
H100** (a 5s clip in ~5s). Serve it as a **warm, persistent microservice** the
existing backend calls. Target: a highlighted passage → watchable frames in
**well under 2 minutes**.

## What we're building NOW (proof-of-concept, single-GPU)
A working end-to-end render path. **Optimizations (the 2-GPU split) come later.**

```
Frontend (highlight)
  └─► app/ FastAPI  ── POST /api/generate-video ──►  app/routers/video_jobs.py
                                                        │ (HTTP, not subprocess)
                                                        ▼
                          services/renderer/  (NEW · own venv · GPU · model warm)
                            main.py     FastAPI :8004   /health   /render
                            renderer.py wraps StreamingCF (vendored engine)
                              per shot: StreamingCF → frames → shot mp4
                              ffmpeg concat → final_story.mp4
                                                        │
                                                        ▼
                                   generated_videos/video_<ts>.mp4  → UI
```

Why a separate service + own venv: the engine needs `torch>=2.4 / diffusers
0.31 / flash-attn`, which **conflict** with the backend's pinned `torch==2.1`.
It's an HTTP boundary just like the existing vLLM/audio services.

## What we take from `_SPED` (and skip for the PoC)
- **TAKE:** `spedcopycausalforcing/{wan,pipeline,utils,configs,demo_utils,cf_streaming.py}`
  — vendored into `services/renderer/vendor/`. The streaming API is ready-made:
  `load_cf_pipeline()` (load once) + `StreamingCF.start/step/decode_chunk`.
  Default model: **chunkwise 4-step** (`chunkwise/causal_forcing.pt`), 480×832,
  81 frames (~5s) per shot.
- **SKIP for now (roadmap):** voice/ASR (Whisper, Flask live UI), SPEED (~1.09×
  on the distilled model), custom CUDA kernels, and the **2-GPU DiT/VAE split**.

## Multi-shot handling (PoC)
Our shot planner already splits a scene into shots. Render each shot as one
≤5s clip via the fast model, then **ffmpeg-concat** — same stitch step
`generate_video.py` already does. (One unbroken stream via mid-gen prompt-swap
is a later phase.)

## Deferred: the 2-GPU compute split (how `_SPED` did it — for later)
Their real split lives in `_SPED/controllable world model/minWM/Wan21/live/`
(`streaming_worker.py` + `server.py`): **stage-level pipeline parallelism**, not
tensor parallelism. DiT/generator on `cuda:0`, VAE on `cuda:1`; `gen_step()`
returns latents, `decode_step()` does `latents.to(vae_device)` then decodes; a
`Queue(maxsize=4)` overlaps "generate chunk N+1" with "decode chunk N", cutting
per-chunk time from `gen+decode` to `max(gen,decode)` (~28%). Our `StreamingCF`
already has the same `step()`/`decode_chunk()` split, so this drops in later as
a device-param + a two-thread queue loop. **We measure gen-vs-decode ms first
(the worker prints it) before deciding it's worth the complexity.**

## Build steps
- [x] **0. Vendor engine** → `services/renderer/vendor/` (wan, pipeline, utils,
      configs, demo_utils, cf_streaming.py; `pipeline/__init__.py` trimmed to inference).
- [x] **1. Renderer service** — `services/renderer/{main,renderer,schema}.py`,
      `requirements.txt`. Model loaded warm in FastAPI `lifespan`; `POST /render`
      takes a video plan + output dir, returns the final mp4 path. One render at
      a time (asyncio lock — single GPU).
- [x] **2. Backend wire-in** — `app/routers/video_jobs.py` calls the renderer over
      HTTP instead of subprocessing `generate_video.py`; job tracking / polling /
      `generated_videos/` copy and all `/video-jobs/*` endpoints unchanged.
      `app/config.py` gains `renderer_url` + `render_seconds_per_shot`.
- [x] **3. VM scripting** — `scripts/setup_renderer.sh` + `make {setup,smoke,start}-renderer`.
- [ ] **4. RUN ON VM** — clone, `hf auth login`, `make setup-renderer`, smoke test,
      start services, verify end-to-end (this is the remaining step; needs the GPU VM).

## How to run it (on the Primetime GPU VM)
> Steps marked **YOU** are interactive — run them in-session with a leading `! `.

1. **YOU:** spin up a single **H100 80GB** instance; `git clone` this repo onto it.
2. **YOU:** `hf auth login` (Wan weights are gated).
3. `bash scripts/setup_renderer.sh` — creates `.venv-renderer`, installs deps +
   `flash-attn`, downloads weights into `services/renderer/vendor/{wan_models,checkpoints}`
   (~20–30GB → keep on a **persistent volume**, symlinked in).
4. **Phase 0 smoke (prove frames generate):**
   `CUDA_VISIBLE_DEVICES=0 .venv-renderer/bin/python services/renderer/vendor/cf_streaming.py --seconds 5`
   → writes an mp4, prints `N frames in Xs = Y FPS` (expect ~16–22).
5. Start services: Postgres + `app/` backend (existing venv) + the renderer
   (`make start-renderer`). Tunnel only the frontend port to your laptop.

## Files
**New:** `services/renderer/{__init__,main,renderer,schema}.py`,
`services/renderer/requirements.txt`, `services/renderer/vendor/` (engine),
`scripts/setup_renderer.sh`, `PLAN.md`.
**Modified:** `app/routers/video_jobs.py`, `app/config.py`, `Makefile`, `.gitignore`.
**Reused as-is:** `app/scene_composer.py`, `app/video_prompting.py`,
`app/routers/books.py`, the frontend.

## Verification
1. **Engine smoke** (step 4 above) — frames + FPS.
2. **Renderer unit:** `POST :8004/render` with a hand-written 1–2 shot plan → playable mp4 in seconds.
3. **Integration:** `POST /api/generate-video` → poll `/api/video-jobs/{id}` → `done` → clip plays.
4. **Full UX:** highlight a Frankenstein passage in the UI → Generate Video → clip saved to
   `generated_videos/`, **under 2 minutes**.

## Risks
- Gated + large weights (~20–30GB): `hf auth login` + persistent volume.
- `flash-attn` build: 10–20 min, CUDA/torch-version sensitive; prefer a matching prebuilt wheel.
- 81-frame cap → PoC stitches ≤5s shots (accepted).
- `negative_prompt`: the distilled streaming path may ignore it; verify (low risk, look anchors are in the prompt).
