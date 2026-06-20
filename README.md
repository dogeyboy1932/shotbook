# book_video_gen

Backend for an interactive book-to-video platform: a reader highlights a
paragraph, the system generates a 30s cinematic clip with cloned-voice
dialogue (XTTS) and SFX (Stable Audio). To support generating *any*
paragraph on demand (not just sequentially), each book is pre-ingested into
a 3-tier Postgres schema so a paragraph can be resolved into a fully
self-contained prompt payload without re-deriving context from scratch.

Infra target: PostgreSQL + FastAPI + multi-GPU vLLM
(Llama-3-70B-Instruct or Qwen2-72B-Instruct).

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
app/routers/generate_context.py     -- GET /api/generate-context/{paragraph_id}
ingestion/schemas.py                -- Pydantic schemas the LLM is forced into (structured outputs)
ingestion/llm_client.py             -- GpuWorkerPool: round-robins across vLLM replicas
ingestion/orchestrator.py           -- two-pass ingestion pipeline + CLI entrypoint
scripts/install_vm.sh               -- fresh-VM setup: OS deps, Postgres, venv, vLLM
scripts/launch_vllm_cluster.sh      -- detects GPU count, launches TP-grouped vLLM replicas
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
script for details; it's idempotent and safe to re-run.

Then:

```bash
source .venv/bin/activate

# Launch the vLLM cluster (auto-detects GPU count; see HANDOFF.md for the
# tensor-parallel sizing rationale). Model name must be exported, not just
# set in .env -- the launch script reads the shell environment.
export BVG_VLLM_MODEL_NAME=meta-llama/Meta-Llama-3-70B-Instruct   # or any vLLM-served model
./scripts/launch_vllm_cluster.sh

# Ingest a book (see data/texts/ for samples)
PYTHONPATH=. python -m ingestion.orchestrator data/texts/poe-the-masque-of-the-red-death.txt \
    --title "The Masque of the Red Death" --author "Edgar Allan Poe"

# Serve the API
PYTHONPATH=. uvicorn app.main:app --port 8080
curl http://localhost:8080/api/generate-context/1
```

If you only need the API/DB and not real ingestion, skip the vLLM steps --
`app/config.py` only requires `BVG_DATABASE_URL` to serve already-ingested
data.

## Schema model (read before changing anything)

- **Tier 1** (`characters`, `locations`): immutable baseline visual/voice/SFX prompts.
- **Tier 2** (`character_states`, `location_states`): append-only ledger,
  valid over `[valid_from_paragraph_id, valid_until_paragraph_id)`. Each row
  holds the entity's full current state (not a sparse patch), so the API
  read path never backfills across rows.
- **Tier 3** (`paragraphs` + `paragraph_characters`): one row per paragraph;
  `sequence_index` is the real timeline axis for all temporal joins.

Full rationale in [HANDOFF.md](HANDOFF.md) and [data_pipeline.md](data_pipeline.md).
