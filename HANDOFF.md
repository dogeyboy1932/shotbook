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
ingestion/schemas.py                -- Pydantic schemas the LLM is forced into (guided_json)
ingestion/llm_client.py             -- GpuWorkerPool: round-robins across vLLM replicas
ingestion/orchestrator.py           -- two-pass ingestion pipeline + CLI entrypoint
scripts/launch_vllm_cluster.sh      -- detects GPU count, launches TP-grouped vLLM replicas
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
python3 -m venv .venv && source .venv/bin/activate
pip install -r requirements.txt
pip install vllm   # not in requirements.txt -- heavy CUDA dep, install separately

# Postgres (Docker is easiest)
docker run -d --name bvg_pg -e POSTGRES_PASSWORD=postgres -p 5432:5432 postgres:16
docker exec -i bvg_pg psql -U postgres -d postgres < db/schema.sql

# .env
echo 'BVG_DATABASE_URL=postgresql+asyncpg://postgres:postgres@localhost:5432/postgres' >> .env

# GPUs (only needed for ingestion, skip if just testing API/schema)
./scripts/launch_vllm_cluster.sh

# Ingest a book
PYTHONPATH=. python -m ingestion.orchestrator path/to/book.txt --title "..." --author "..."

# Serve the API
uvicorn app.main:app --reload --port 8080
curl http://localhost:8080/api/generate-context/1
```

## What has actually been tested (in a Docker Postgres + venv, this session)

- `db/schema.sql` applies cleanly on Postgres 16.
- Full ORM round trip: insert characters/locations/paragraphs/states,
  confirmed the LATERAL-join compile query in
  `app/routers/generate_context.py` correctly resolves Tier 2 carry-forward
  state (paragraph 2 correctly inherited paragraph 1's delta).
- Full FastAPI endpoint via `TestClient`: 200 with correct payload, 404 for
  missing paragraph_id.
- `ingestion.orchestrator.ingest_book()` end-to-end with `GpuWorkerPool.extract_structured`
  mocked (no real vLLM available in this sandbox) — chapter detection,
  registry dedup, sequential beat writes, and Tier 2 carry-forward all
  verified correct.
- `scripts/launch_vllm_cluster.sh` logic verified with stubbed
  `nvidia-smi`/`curl`/`python` binaries — GPU grouping, health-check loop,
  and `.env` write/overwrite all confirmed correct.

## What has NOT been tested

- Real vLLM server behavior (`guided_json` structured decoding, actual
  Llama-3-70B/Qwen2-72B output quality) — no GPU available in this sandbox.
- Real multi-GPU tensor-parallel memory fit/throughput on actual H100s.
- Any real book ingested end to end with a live model.

## Known open question

If you sometimes have only 1 GPU available, the current script/model combo
can't run (TP=2 minimum for fp16 70B). Decide whether to add an automatic
fallback to a quantized checkpoint for the 1-GPU case, or just treat 1-GPU
runs as unsupported.
