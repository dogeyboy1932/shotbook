# ShotBook — Session Handoff (video-generation pipeline)

Hand this to a fresh Claude Code session to continue. It covers **everything**
done so far, the current live state of the GPU box, the decisions made (and
reversed), where outputs land, how to resume, and what's left.

> TL;DR of where we are: a reader highlights a passage → backend resolves the
> book's world-state from Supabase → Claude plans a shot breakdown → the **fast
> 1.3B streaming renderer** generates the video as **ONE seamless continuous
> rollout** (no stitching) and saves `final_story.mp4`. It works end to end.
> The heavier 5B "HD" model was added then **dropped** (too slow). Next up is
> wiring the **live frame stream** into the browser.

---

## 1. What ShotBook is

Interactive book-to-video. The reader highlights a paragraph in a web reader;
the system generates a short cinematic clip **grounded in the resolved
story-state** at that point in the book (characters, location, look). The book
is pre-ingested into a 3-tier Postgres schema so any paragraph can be resolved
into a self-contained prompt payload on demand.

Pipeline: **highlight → resolve state (Postgres) → plan shots (Claude) → render
(1.3B streaming model) → seamless mp4 → play in browser.**

---

## 2. CURRENT LIVE STATE (read this first)

### GPU VM (Prime Intellect / "Primetime")
- **SSH:** `ssh root@86.38.238.67 -p 22 -i private_key.pem` (user is `root`, home `/root`)
- **Hardware:** 1× NVIDIA H100 80GB, Ubuntu 22.04, Python 3.10, ~420GB disk free.
- **Repo on VM:** `/root/shotbook`
- **Services run in tmux session `sb`** (windows):
  - `backend`  — FastAPI :8080  (`.venv-api`)            ← Supabase + Claude
  - `renderer` — FastAPI :8004  (`.venv-renderer`, GPU)  ← fast 1.3B, model warm
  - (`quality` :8005 / 5B was here — **killed**, see §5)
  - Reattach: `tmux attach -t sb`. List: `tmux list-windows -t sb`.
- **GPU usage:** ~26GB (1.3B renderer warm). ~55GB free.

### Your laptop
- **SSH tunnel:** `ssh -N -L 8080:localhost:8080 root@86.38.238.67 -p 22 -i private_key.pem`
  (runs as a background process; backend reachable at `localhost:8080`).
- **Frontend:** Vite dev server on `http://localhost:5173` (`cd frontend && npm run dev`).
  `vite.config.ts` proxies `/api` → `localhost:8080`, so the browser only needs :5173.
- **Open `http://localhost:5173`** → pick *The Tell-Tale Heart* → highlight a
  paragraph → **Query** (state appears) → **Generate (Fast)** → seamless clip plays.

### Database (Supabase, already ingested)
- Reached via the **pooler** (the VM is IPv4-only; the direct host is IPv6-only):
  `aws-1-us-east-1.pooler.supabase.com:5432`, user `postgres.sacfmewznnybjawazyrq`,
  db `postgres`. Note `aws-1` (not `aws-0`).
- **TLS:** verified against Supabase's private CA, extracted to
  `/root/supabase_ca.pem` (do NOT disable verification — a classifier blocks it).
- **Two books already ingested:** *The Tell-Tale Heart* (book_id 1, 19 paras),
  *The Black Cat* (book_id 2, 31 paras). Ingestion status `beats_pass_complete`.

### Latency (measured this session, fast path, 1 paragraph)
- State query + Claude shot planning: **~11s** (POST `/api/generate-video` returns the plan).
- Render (4 shots): **~61s**. **Total highlight→playable ≈ 72s.**

---

## 3. The journey (what we did, in order)

1. **Original bottleneck:** the team's `generate_video.py` drove Wan2.2-**14B**,
   ~40 min/clip. Unusable for interactivity.
2. **Integrated `_SPED`'s streaming engine** (a separate hackathon repo): the
   **Causal-Forcing / Self-Forcing distilled Wan2.1-T2V-1.3B** model, which
   generates **chunk-by-chunk autoregressively at ~16-22 FPS** on one H100. The
   self-contained engine was **vendored** into `services/renderer/vendor/`.
3. **Built the renderer microservice** (`services/renderer/`, own venv) and
   wired the existing backend to POST shot plans to it instead of subprocessing
   a cold model. Proved end-to-end on the H100.
4. **Deployed on the Primetime H100 VM**; solved Supabase pooler + private-CA
   TLS, and installed **flash-attn via a prebuilt wheel** (no nvcc on the box).
5. **Added a 5B "HD" hybrid** (Wan2.2-TI2V-5B via diffusers) as a second warm
   service on :8005, routed by a `quality` flag — fast 1.3B for preview, 5B for
   a polished render.
6. **DROPPED the 5B.** User judged the 5B quality not meaningfully better for
   the effort, and it's far too slow (~2.5 min **per shot**, one-shot diffusion,
   **cannot stream/real-time**). Killed the :8005 service; 1.3B is the sole path.
7. **Added live MJPEG streaming** to the renderer (`POST /render/stream`) so
   frames can be displayed the moment they're produced.
8. **Made rendering SEAMLESS (no stitching):** render a whole passage as ONE
   continuous autoregressive rollout, morphing the prompt at shot boundaries via
   `ramp_to` (SLERP) while the KV-cache carries the picture forward. Verified:
   a 2-shot render produces a single `final_story.mp4`, **zero per-shot files**,
   and frames flow continuously across the boundary (see `seamless_test2.mp4`).

### Decisions / north star (per the user)
- **Quality is later.** Priority is the **app working** and **fast, real-time-ish
  generation** as the reader visualizes the passage.
- The valued `_SPED` property is **seamless frame-to-frame continuity** (add
  frames one after another, no seams) — achieved via the single-rollout change.
- Not trying to replicate `_SPED`'s full interactive prompt-injection UX — just
  fast generation + seamlessness.

---

## 4. Architecture & key files

```
app/                          FastAPI backend (.venv-api), port 8080
  config.py                   settings (BVG_ env prefix). renderer_url=:8004,
                              quality_renderer_url=:8005 (5B, now unused),
                              render_seconds_per_shot=5.0
  db.py                       async SQLAlchemy; TLS via BVG_DB_SSL_CA (ssl ctx)
  schemas.py                  ComposeSceneRequest{paragraph_ids, quality}
  scene_composer.py / video_prompting.py / context_compiler.py
                              resolve state + assemble shot prompts (uses Claude)
  routers/
    video_jobs.py             POST /api/generate-video (plan+thread), routes to
                              renderer_url or quality_renderer_url by `quality`;
                              GET /api/video-jobs/{id} (poll), /{id}/video (serve)
    generate_context.py, books.py, compose_scene.py ...

services/renderer/            Renderer microservices
  main.py                     FastAPI :8004 (.venv-renderer). /health, /render,
                              POST /render/stream (live MJPEG multipart)
  renderer.py                 RenderEngine: warm-loads Causal-Forcing 1.3B once.
                              stream_plan() = SEAMLESS single rollout generator
                              (ramp_to/hardcut at boundaries) + saves mp4.
                              render_plan() delegates to stream_plan (drains it).
  schema.py                   RenderRequest{shots, out_dir, seconds_per_shot,
                              negative_prompt, steps?, guidance?}, RenderResponse
  quality_engine.py / quality_main.py
                              5B HD service (:8005). CODE KEPT but service is
                              stopped and dropped from the active flow.
  vendor/                     vendored _SPED engine: cf_streaming.py (StreamingCF:
                              start/step/decode_chunk/hardcut/ramp_to), wan/,
                              pipeline/, utils/, configs/. Weights live under
                              vendor/wan_models/ + vendor/checkpoints/ (gitignored).

frontend/ (Vite/React, runs on laptop)
  src/api.ts                  generateVideo(ids, quality), queryContext, etc.
  src/pages/Reader.tsx        highlight → query → handleCompose(quality)
  src/components/ContextPanel.tsx
                              "Generate (Fast)" + "Render in HD" buttons,
                              video player. (HD button points to dead :8005 — see §6)

scripts/
  deploy.sh                   ONE-COMMAND bring-up from laptop: scripts/deploy.sh <VM_IP>
  deploy.config               gitignored secrets (key path, DB URL, Anthropic key)
  deploy.config.example       template
  resume_vm.sh                rebuild venvs + weights + CA on a fresh box

startup.txt, VM_SETUP.md, PLAN.md   design + runbooks
```

### The renderer engine (how seamlessness works)
`StreamingCF` (in `vendor/cf_streaming.py`): `start(prompt, total_frames)` allocs
noise + KV/cross-attn caches; `step()` denoises one chunk (KV-cache persists =
visual continuity); `decode_chunk()` → uint8 RGB frames; `hardcut(p)` swaps
conditioning (re-encode + reset cross-attn only); `ramp_to(p, k)` SLERP-morphs
the prompt embedding over k chunks. `nfpb` = latent frames/chunk; `LATENT_FPS=4`
(1 latent → 4 pixel frames); output 480×832 @ 16 fps.

**Seamless rollout (`renderer.py: stream_plan`):** sum each shot's latent budget,
`start()` ONE rollout for the whole passage, and at each shot boundary call
`ramp_to(next_prompt, k)` (k=4 for a declared scene change, k=8 to hold
continuity). **Key insight:** a single rollout is *one continuously-morphing
shot* — a `hardcut` to an UNRELATED prompt hallucinates the new prompt into the
old frame (we saw a "glowing blue eye-face" floating in the bedroom). So we
always SLERP-morph, and the planner's consecutive shots should be **related**
(same scene evolving) for best results.

---

## 5. Where outputs go (`final_story.mp4`)

The renderer writes `final_story.mp4` into the `out_dir` of the render request.
- **Backend jobs:** `out_dir = /root/shotbook/video_jobs/<job_id>/` →
  `final_story.mp4` there, then **copied** to
  `/root/shotbook/generated_videos/video_<YYYYMMDD_HHMMSS>.mp4`. The browser
  plays that copy via `GET /api/video-jobs/<job_id>/video` (FileResponse).
- **Test renders (this session):** `/root/shotbook/generated_videos/{seam2,seam_test,smoke,...}/final_story.mp4`.
- **Pulled to the laptop (project root):** `seamless_test2.mp4` (latest seamless),
  `seamless_test.mp4`, `smoke_test.mp4`, `compare_5B_5s_720p.mp4`, etc.
- Backend-generated videos are NOT auto-downloaded; `scp` them from the VM's
  `generated_videos/` if you want them locally.

---

## 6. Known issues / gotchas

- **"Render in HD" button is currently broken:** it routes to the 5B service on
  :8005, which is **stopped**. Either remove the button (recommended — see §8) or
  restart the 5B (`tmux new-window -t sb -n quality "cd ~/shotbook; CUDA_VISIBLE_DEVICES=0 .venv-quality/bin/uvicorn services.renderer.quality_main:app --host 0.0.0.0 --port 8005 > ~/quality.log 2>&1"`).
- **Live MJPEG isn't wired to the browser yet.** `POST /render/stream` exists and
  works (verified by curl), but the backend relay + frontend `<img>` are NOT
  built. The browser still gets the finished seamless mp4 after render. (§8)
- **SSH throttling:** the box drops rapid/multiplexed connects (frequent exit
  255). Use a fresh `ControlMaster` socket and don't hammer it. Long jobs: run in
  tmux on the VM (it SIGHUPs detached nohup/setsid processes on logout).
- **Seamless ≠ hard cuts:** see §4. Keep shot prompts related; rely on ramp morph.
- **Claude planning ~11s** is now the biggest fixed latency before any frame.
- **5B is one-shot diffusion** — it physically cannot stream / do real-time.
  Real-time *and* quality would need a distilled streaming model like
  **Krea-Realtime-14B** (documented future option, heavier integration).

---

## 7. Deploy / resume a NEW instance (fast path)

Everything persistent lives in `scripts/deploy.config` (gitignored). Only the IP
changes per instance.

```bash
# from the laptop, in the repo root:
scripts/deploy.sh <NEW_VM_IP>
```
`deploy.sh` probes the box, rsyncs the repo, writes the VM `.env` (DB URL +
Anthropic key from deploy.config; CA path derived from remote $HOME), runs
`resume_vm.sh` (skipped if already built), starts services in tmux, opens the
tunnel, starts the frontend, prints the URL.

`resume_vm.sh` builds 3 venvs: `.venv-api`, `.venv-renderer` (torch 2.5.1+cu121
+ **flash-attn prebuilt wheel** `2.7.4.post1+cu12torch2.5cxx11abiFALSE-cp310` +
Wan2.1-1.3B + Causal-Forcing `chunkwise/causal_forcing.pt`), and `.venv-quality`
(5B — see §8, candidate for removal). It also extracts the Supabase CA. It is
**root-safe** (skips `sudo` when absent).

**deploy.config keys:** `VM_SSH_KEY` (abs path, may contain spaces),
`VM_USER=root`, `VM_PORT=22`, `BVG_DATABASE_URL`, `ANTHROPIC_API_KEY`,
`LOCAL_BACKEND_PORT=8080`.

### Manual start (if not using deploy.sh)
```bash
tmux new -s sb
# backend:
cd ~/shotbook && set -a; . ./.env; set +a; PYTHONPATH=. .venv-api/bin/uvicorn app.main:app --host 0.0.0.0 --port 8080
# renderer (Ctrl-b c for a new window):
cd ~/shotbook && CUDA_VISIBLE_DEVICES=0 .venv-renderer/bin/uvicorn services.renderer.main:app --host 0.0.0.0 --port 8004
```

---

## 8. Next steps (recommended order)

1. **Wire the live frame stream into the browser** (the "fast/real-time" goal):
   - Backend: `POST /api/compose-live` (plan + store plan by `stream_id`, return
     `{stream_id, scene}`) and `GET /api/video-stream/{stream_id}` that relays the
     renderer's `POST /render/stream` multipart to the client (httpx stream →
     StreamingResponse, same `multipart/x-mixed-replace; boundary=frame`).
   - Frontend: on generate, set `<img src="/api/video-stream/{id}">` so frames
     appear live as they generate; the finished mp4 is still saved on the VM.
   - The renderer side (`/render/stream`, `stream_plan`) is **already done**.
2. **Remove the "Render in HD" button** (and revert `quality` flag plumbing) so
   the app is cleanly 1.3B-only and has no broken button. Optionally delete the
   5B from `resume_vm.sh`/`deploy.sh` (drops the 32GB download → faster resume).
3. **Cut the ~11s planning latency** (smaller/faster planner call, cache, or
   overlap planning with the first shot's render).
4. (Later) quality: revisit only if needed — Krea-Realtime-14B is the "real-time
   AND quality" path; the 5B code is still in the repo if wanted.

---

## 9. Secrets — NEVER commit these (all gitignored)
- `.env` (live `ANTHROPIC_API_KEY`), `supabase.txt` (has `sb_secret_…` + DB
  password `primeintelligen`), `private_key.pem` / `*.pem`,
  `scripts/deploy.config`. Do not disable TLS verification (classifier-blocked).
- `/tmp/vm.env` (laptop) holds the VM `.env` contents used to seed deploy.config.

---

## 10. Commit history (this session's work, newest first)
```
e953db5 Seamless single-rollout rendering (no stitching)
34fe821 Add live MJPEG streaming to the fast renderer
f431b30 Add per-request steps/guidance override to the 5B HD renderer
415423d Add hybrid renderer: fast 1.3B preview + cinematic 5B HD path   <- 5B (now dropped)
8e89dc0 Add one-command GPU deploy script
df422c7 Make resume_vm.sh root-safe + add startup guide
0e57cd9 Preserve VM work: Supabase SSL, frontend video player, 5B quality path, resume script
2880c10 Add fast streaming video renderer (Wan2.1-1.3B) to replace 40-min 14B path
```

---

## 11. Reference: ingestion / schema (pre-existing, still relevant)

The book is ingested into a **3-tier Postgres schema** (`db/schema.sql`):
- **Tier 1** (`characters`, `locations`): immutable baseline visual/voice/SFX prompts.
- **Tier 2** (`character_states`, `location_states`): append-only ledger valid over
  `[valid_from_paragraph_id, valid_until_paragraph_id)`; each row holds the FULL
  current state (carry-forward), so reads just take the latest row.
- **Tier 3** (`paragraphs` + `paragraph_characters`): one row/paragraph;
  `sequence_index` is the real timeline axis.

Ingestion (`ingestion/orchestrator.py`) is a two-pass LLM pipeline (Tier-1
registry pass, then per-paragraph beat pass). The OLD design used local **vLLM**
(Qwen/Llama 70B) for ingestion — but the **live system now uses the Anthropic
API (Claude)** for shot planning at request time (`ANTHROPIC_API_KEY`), and the
two demo books are already ingested into Supabase, so vLLM is not currently in
the loop. The vLLM cluster scripts (`scripts/launch_vllm_cluster.sh`,
`install_vm.sh`) remain in the repo for re-ingesting new books. Detailed
ingestion bugs/fixes are in git history (commit `0e57cd9` and earlier) if you
re-run ingestion.
