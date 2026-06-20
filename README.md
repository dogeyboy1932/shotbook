# book_video_gen

Backend (+ a small reader frontend) for an interactive book-to-video
platform: a reader highlights a paragraph, the system generates a 30s
cinematic clip with cloned-voice dialogue (XTTS) and SFX (Stable Audio). To
support generating *any* paragraph on demand (not just sequentially), each
book is pre-ingested into a 3-tier Postgres schema so a paragraph can be
resolved into a fully self-contained prompt payload without re-deriving
context from scratch.

Two separate LLM paths, used for different things:

- **Book ingestion** (offline, batch) runs on a local multi-GPU **vLLM**
  fleet (Llama-3-70B-Instruct, Qwen2.5-32B-Instruct, or any vLLM-served
  model) -- this is the embarrassingly-parallel job that extracts the
  character/location registry and per-paragraph beats for an entire book.
- **`POST /api/compose-scene`** (online, one request per reader query) calls
  the **Claude API** directly to plan the video shot breakdown for whatever
  passage the reader just highlighted. It does not touch the vLLM fleet.

Infra target: PostgreSQL + FastAPI + a Vite/React reader frontend + a
multi-GPU vLLM fleet for ingestion + the Claude API for on-demand shot
planning.

See [HANDOFF.md](HANDOFF.md) for the full design rationale, schema model,
and session-to-session status (what's tested, what isn't, open questions).

## Repo layout

```
db/schema.sql                       -- Tier 1/2/3 Postgres DDL
app/config.py                       -- env-driven settings (BVG_ prefix, .env file)
app/db.py                           -- async SQLAlchemy engine/session
app/models.py                       -- ORM models mirroring schema.sql
app/schemas.py                      -- Pydantic response models for the API
app/main.py                         -- FastAPI app
app/context_compiler.py             -- resolves Tier 1 + Tier 2 state for a set of paragraph_ids
app/scene_composer.py               -- merges per-paragraph state into one scene (deterministic)
app/video_prompting.py              -- Claude API: plans the per-scene video shot breakdown
app/routers/generate_context.py     -- GET /api/generate-context/{paragraph_id}
app/routers/books.py                -- library/reader/compose-scene endpoints
ingestion/schemas.py                -- Pydantic schemas the LLM is forced into (structured outputs)
ingestion/llm_client.py             -- GpuWorkerPool: round-robins across vLLM replicas (ingestion only)
ingestion/orchestrator.py           -- two-pass ingestion pipeline + CLI entrypoint
scripts/install_vm.sh               -- fresh-VM setup: OS deps, Postgres, venv, vLLM
scripts/launch_vllm_cluster.sh      -- detects GPU count, launches TP-grouped vLLM replicas
frontend/                           -- Vite/React reader UI (highlight -> query -> compose scene)
data/texts/                         -- sample public-domain books for testing ingestion
requirements.txt
```

## Quickstart on a fresh GPU VM

```bash
./scripts/install_vm.sh
```

This installs OS packages (including Python dev headers, required for
vLLM's CUDA JIT step), starts Postgres in Docker with the schema applied,
creates `.venv`, and installs all Python deps including `vllm`. See the
script for details; it's idempotent and safe to re-run. (If Docker is
already installed via its own apt repo, the script's `docker.io` package
will conflict with it -- just drop that one package from the install and
run the rest of the script's steps manually.)

Then:

```bash
source .venv/bin/activate

# Launch the vLLM cluster for ingestion (auto-detects GPU count; see
# HANDOFF.md for the tensor-parallel sizing rationale). Model name must be
# exported, not just set in .env -- the launch script reads the shell
# environment.
export BVG_VLLM_MODEL_NAME=meta-llama/Meta-Llama-3-70B-Instruct   # or any vLLM-served model
./scripts/launch_vllm_cluster.sh

# Ingest a book (see data/texts/ for samples)
PYTHONPATH=. python -m ingestion.orchestrator data/texts/poe-the-masque-of-the-red-death.txt \
    --title "The Masque of the Red Death" --author "Edgar Allan Poe"

# Set a Claude API key -- needed by POST /api/compose-scene, not by ingestion
echo "ANTHROPIC_API_KEY=sk-ant-..." >> .env

# Serve the API
PYTHONPATH=. uvicorn app.main:app --port 8080
curl http://localhost:8080/api/generate-context/1
```

If you only need the API/DB and not real ingestion, skip the vLLM steps --
`app/config.py` only requires `BVG_DATABASE_URL` to serve already-ingested
data. `POST /api/compose-scene` additionally needs `ANTHROPIC_API_KEY`; every
other endpoint works without it. A missing key surfaces as a `503` from that
one endpoint rather than blocking the rest of the API at startup.

### Running the reader frontend

The frontend is a separate Vite/React app that talks to the FastAPI service
above. Needs Node.js (v20+; this repo was set up with v22 via
[NodeSource](https://github.com/nodesource/distributions)).

```bash
cd frontend
npm install
npm run dev   # serves on :5173
```

`frontend/vite.config.ts` proxies `/api` and `/healthz` to `http://localhost:8080`
server-side, so the browser only ever needs to reach port 5173 -- see below
for why that matters when both processes run on a remote VM.

### Accessing the frontend from your laptop when everything runs on the VM

Run both the backend and frontend **on the VM** exactly as above -- both
stay on `localhost` there, talking to each other directly via the Vite
proxy. From your **laptop**, open one SSH tunnel for the frontend's port
only:

```bash
ssh -L 5173:localhost:5173 you@vm-host
```

Leave that session open (add `-N` if you don't want an interactive shell:
`ssh -N -L 5173:localhost:5173 you@vm-host`), then open
`http://localhost:5173` in your **laptop's** browser. Your browser only ever
talks to local port 5173; the tunnel forwards it to the VM's Vite dev
server, which proxies API calls to the VM's own FastAPI service on 8080.
You don't need to open or tunnel port 8080 separately, and no local
Python/Node/Docker install is needed on the laptop -- only `ssh`.

## `POST /api/compose-scene` response shape

Given the reader's highlighted `paragraph_ids`, this resolves their merged
story state and asks Claude to plan a video shot breakdown. The shape
mirrors a hand-authored `WORLD = {...}; SCENES = [...]; NEG = "..."` script:

```jsonc
{
  // ...resolved characters/location/dialogue/sfx for the span...
  "video": {
    "world": {
      // fixed, full visual-description anchors -- spliced verbatim into
      // every shot's prompt so identity/setting never drift between shots
      "characters": { "Robert Walton": "...", "Lieutenant": "..." },
      "location": "...",
      "look": "Photorealistic cinematic film still, 35mm lens, ..."
    },
    "shots": [
      {
        "shot_id": "01_the_question",
        "camera": "...",
        "action": "...",
        "light": "...",
        // "continuous_frame": clip opens on the previous clip's final frame
        // (one unbroken take). "cut_same_scene": ordinary edit cut to a new
        // angle/subject but still the same scene (e.g. shot/reverse-shot
        // dialogue) -- no location or time change. "cut_new_scene": an
        // actual scene break (location change, time jump). Always
        // "cut_new_scene" for the first shot.
        "continuity": "cut_new_scene",
        "prompt": "...fully assembled text-to-video prompt..."
      }
    ],
    "negative_prompt": "morphing, warping, ..."
  },
  "audio_prompt": "..."
}
```

If `ANTHROPIC_API_KEY` isn't configured, this endpoint returns `503` instead
of the payload above. Every other endpoint (library, reader, the batch
`generate-context` resolver) is unaffected -- they only need Postgres.

## Schema model (read before changing anything)

- **Tier 1** (`characters`, `locations`): immutable baseline visual/voice/SFX prompts.
- **Tier 2** (`character_states`, `location_states`): append-only ledger,
  valid over `[valid_from_paragraph_id, valid_until_paragraph_id)`. Each row
  holds the entity's full current state (not a sparse patch), so the API
  read path never backfills across rows.
- **Tier 3** (`paragraphs` + `paragraph_characters`): one row per paragraph;
  `sequence_index` is the real timeline axis for all temporal joins.

Full rationale in [HANDOFF.md](HANDOFF.md) and [data_pipeline.md](data_pipeline.md).
