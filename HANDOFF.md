# book_video_gen — Session Handoff

Paste this file into a new Claude Code session (or just `cat` it and reference
the path) on another machine to pick up exactly where this session left off.

## What this project is

Backend for an interactive book-to-video platform: a reader highlights a
paragraph, the system generates a 30s cinematic clip with cloned-voice
dialogue (XTTS) and SFX (Stable Audio). To support generating *any*
paragraph on demand (not just sequentially), the book is pre-ingested into a
3-tier Postgres schema so each paragraph can be resolved into a fully
self-contained prompt payload without re-deriving context from scratch.

Infra target: PostgreSQL + FastAPI + 8x H100 running vLLM
(Llama-3-70B-Instruct or Qwen2-72B-Instruct).

## Repo layout

```
db/schema.sql                       -- Tier 1/2/3 Postgres DDL
app/config.py                       -- env-driven settings (BVG_ prefix, .env file)
app/db.py                           -- async SQLAlchemy engine/session
app/models.py                       -- ORM models mirroring schema.sql
app/schemas.py                      -- Pydantic response models for the API
app/main.py                         -- FastAPI app
app/routers/generate_context.py     -- GET /api/generate-context/{paragraph_id}
ingestion/schemas.py                -- Pydantic schemas the LLM is forced into (structured outputs)
ingestion/llm_client.py             -- GpuWorkerPool: round-robins across vLLM replicas
ingestion/orchestrator.py           -- two-pass ingestion pipeline + CLI entrypoint
scripts/install_vm.sh               -- fresh-VM setup: OS deps, Postgres, venv, vLLM (idempotent)
scripts/launch_vllm_cluster.sh      -- detects GPU count, launches TP-grouped vLLM replicas
data/texts/                         -- sample public-domain books (for ingestion smoke tests)
data/json/                          -- same books pre-structured as JSON (reference data, unused by the pipeline so far)
requirements.txt
```

## Schema model (the part most worth re-reading before changing anything)

- **Tier 1** (`characters`, `locations`): immutable baseline visual/voice/SFX prompts.
- **Tier 2** (`character_states`, `location_states`): append-only ledger. Each
  row is valid over `[valid_from_paragraph_id, valid_until_paragraph_id)`.
  Rows hold the entity's **full current state**, not a sparse patch — the
  orchestrator carries forward any field the LLM left null from the
  previous state when writing a new delta. This means the API's read path
  never needs to backfill across multiple rows; it just takes the latest one.
- **Tier 3** (`paragraphs` + `paragraph_characters`): one row per paragraph.
  `sequence_index` (not `paragraph_id`) is the real timeline axis — all
  temporal joins compare `sequence_index`, not raw FK ordering.

## Ingestion pipeline (`ingestion/orchestrator.py`)

1. `segment_book_into_paragraphs` — deterministic, no LLM. Splits on blank
   lines, detects `Chapter N` headings via regex.
2. **Pass 1** — large chunks (~40 paragraphs) fanned out concurrently across
   the GPU pool to extract Tier 1 candidates; merged/deduped by lowercased
   name+aliases; written once.
3. **Pass 2** — registry injected as system-prompt context; paragraphs
   batched (default 8) and fanned out concurrently for beat extraction
   (camera framing, dialogue, SFX, state deltas). LLM calls run in parallel
   (out of order); **writes are sequential** in `sequence_index` order with
   an in-memory carry-forward cache, since Tier 2 correctness depends on
   strict book order.
4. Per-chunk LLM failures are logged and skipped, not fatal to the whole run.

## GPU topology — the part that surprised us mid-session

vLLM data-parallel replicas only work if the model **fits on one GPU**.
Llama-3-70B fp16 needs ~140GB — doesn't fit on a single 80GB H100. So:

- 1 GPU: can't run the fp16 model at all (would need a quantized AWQ/GPTQ build).
- 2 GPUs: exactly 1 tensor-parallel (`--tensor-parallel-size 2`) replica = **1 endpoint**.
- 8 GPUs: 4 TP=2 replicas = **4 endpoints** (true data parallelism kicks in here).

`scripts/launch_vllm_cluster.sh` handles this automatically: it counts GPUs
via `nvidia-smi` at run time (not hardcoded), groups them into TP-sized
replicas, launches one vLLM server per group, health-checks them, and writes
`BVG_VLLM_ENDPOINTS=[...]` (JSON list) into `.env`. `app/config.py` reads
that `.env` automatically via pydantic-settings.

```bash
./scripts/launch_vllm_cluster.sh [tensor_parallel_size=2] [base_port=8000]
```

Known fixed bug: the platform's `seq -s,` appends a trailing comma to
`CUDA_VISIBLE_DEVICES` (`0,1,` instead of `0,1`) — already patched with
`sed 's/,$//'` in the script. If you port this to another shell/OS, re-check
that `seq` behavior before trusting GPU pinning.

## How to run (fresh machine)

```bash
./scripts/install_vm.sh   # OS deps (incl. python3-dev -- see gotcha below), Postgres, venv, vllm

source .venv/bin/activate

# GPUs (only needed for real ingestion, skip if just testing API/schema).
# Model name must be `export`ed -- the launch script reads the shell env,
# NOT .env (.env is only read by the Python app via pydantic-settings).
export BVG_VLLM_MODEL_NAME=meta-llama/Meta-Llama-3-70B-Instruct
./scripts/launch_vllm_cluster.sh

# Ingest a book (data/texts/ has public-domain samples for smoke testing)
PYTHONPATH=. python -m ingestion.orchestrator data/texts/poe-the-masque-of-the-red-death.txt \
    --title "The Masque of the Red Death" --author "Edgar Allan Poe"

# Serve the API
PYTHONPATH=. uvicorn app.main:app --reload --port 8080
curl http://localhost:8080/api/generate-context/1
```

If `install_vm.sh` already ran on this box, you can skip straight to
`source .venv/bin/activate`.

## What has actually been tested (real GPU boxes, Docker Postgres + venv)

- `db/schema.sql` applies cleanly on Postgres 16.
- Full ORM round trip: insert characters/locations/paragraphs/states,
  confirmed the LATERAL-join compile query in
  `app/routers/generate_context.py` correctly resolves Tier 2 carry-forward
  state (paragraph 2 correctly inherited paragraph 1's delta).
- Full FastAPI endpoint, live (not `TestClient`): `GET /api/generate-context/1`
  returns 200 with correct payload, `/api/generate-context/99999` returns 404.
- `scripts/launch_vllm_cluster.sh` against real H100s: GPU grouping,
  health-check loop, and `.env` `BVG_VLLM_ENDPOINTS` write/overwrite all
  confirmed correct.
- A real, live vLLM server (0.23.0) end-to-end, including structured
  decoding — see bugs below. Ingested "The Masque of the Red Death" with
  `Qwen/Qwen2.5-1.5B-Instruct` as a plumbing stand-in: 3 characters, 7
  locations, 14 paragraph beats written, zero errors.

### Real bugs found and fixed while running against a live vLLM server

1. **`ingestion/llm_client.py` sent `"extra_body": {"guided_json": ...}`.**
   `extra_body` is an openai-python SDK convention for flattening params
   into the request body before it's serialized -- meaningless when you
   POST raw JSON via httpx directly, as this client does. The server never
   saw a guided-decoding field at all, so the model free-text answered
   (often wrapped in a ` ```json ` fence), and `json.loads` failed with
   `Expecting value: line 1 column 1`. Separately, `guided_json` itself is
   the *legacy* field name and was removed in newer vLLM releases. Fixed by
   sending `"structured_outputs": {"json": json_schema}` as a top-level
   request field (the current field name as of vLLM 0.23.0 -- re-check this
   if you upgrade vLLM, the field has moved before).
2. **`ingestion/schemas.py`: `ParagraphBeat.camera_framing` was typed as a
   plain `str`**, with the valid enum values only mentioned in the
   `description` text. Structured decoding only constrains *shape*, not
   prose -- so it never enforced the enum, the model returned `"wide"`
   instead of `"wide_shot"`, and Postgres's `CHECK` constraint on
   `paragraphs.camera_framing` rejected the insert. Fixed by making it a
   real `Literal[VALID_CAMERA_FRAMINGS]`.

## What has NOT been tested

- Output *quality* with the actual production model
  (Llama-3-70B-Instruct / Qwen2-72B-Instruct) -- only smoke-tested with a
  1.5B stand-in, which is too weak at entity grounding: it never populated
  `active_character_names`/`active_location_name` on any paragraph, even
  when a character was named directly in the text, so
  `paragraph_characters` and `paragraphs.active_location_id` stayed empty
  for the whole test book. This is a model-capability gap, not a pipeline
  bug -- the orchestrator's `registry.resolve_character`/`resolve_location`
  do link correctly whenever the LLM actually returns a name. Re-verify
  Tier 2/3 linkage once you swap in the real model.
- Real multi-GPU tensor-parallel (TP>1) memory fit/throughput for a 70B+
  model on actual H100s -- the live test above used TP=1 (2 independent
  single-GPU replicas of a small model), not the TP=2 70B configuration.

## Known open question

If you sometimes have only 1 GPU available, the 70B fp16 model can't run
on it (needs ~140GB, TP=2 minimum). Decide whether to add an automatic
fallback to a quantized checkpoint for the 1-GPU case, or just treat 1-GPU
runs as unsupported (small-model smoke testing, as done this session, still
works fine on 1 GPU).
