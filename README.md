# ShotBook

Highlight a passage in a book → get a cinematic video grounded in the book's
resolved world-state, generated **live, frame by frame**.

A reader selects text; the app resolves the exact character/location state at
that point in the story and turns it into a continuous video clip on a GPU —
streamed to the browser as it generates, and steerable in real time.

## Architecture (one diagram)

```
React frontend ──► Supabase           books + paragraphs, resolve_contexts() / delete_book() RPCs
   (frontend/)  └─► VM renderer        POST /generate (plan + render), live MJPEG, /jobs/{id}/steer, /ingest
                     (renderer/, one GPU process)
                       ├─ plan   : Claude shot breakdown grounded in world-state
                       └─ render : Wan2.1-1.3B streaming model (vendor/cf_streaming.py)

Ingestion (ingestion/, Claude → Supabase): two passes write the 3-tier world-state.
```

There is **no middle API tier** — the browser talks only to Supabase and the VM.
Deeper design notes live in [`_docs/ARCHITECTURE.md`](_docs/ARCHITECTURE.md); the
real-time engine and its attribution are in [`_docs/NOVELTY.md`](_docs/NOVELTY.md).

## Repository layout

| Path | What |
|------|------|
| `frontend/` | React + Vite reader/studio UI (Vite reads the root `.env` via `envDir`). |
| `renderer/` | The single VM backend: shot planning (Claude) + streaming render + `/ingest`. Holds the vendored Wan engine in `vendor/`. |
| `ingestion/` | Two-pass Claude pipeline that writes the world-state to Supabase (run as a script or via the UI). |
| `supabase/` | Database: `schema.sql`, `rpc_resolve_contexts.sql`, `migration_cascade_fix.sql`. |
| `example_corpus/` | Public-domain source texts for ingestion smoke tests. |
| `scripts/` | The start scripts (`db-setup`, `start-vm`, `start-tunnel`, `start-frontend`) + `resume_vm.sh`. |
| `_docs/` | Architecture, deploy runbook, and the novelty/attribution doc. |
| `.env.example` | Every config var, in one place. Copy to `.env`. |

## Configure (one file)

Copy `.env.example` → `.env` and fill it in. **Everything** reads from this single
file: frontend `VITE_*` (build-time, via Vite `envDir`), renderer/ingestion
`BVG_*` + `ANTHROPIC_API_KEY`, and the GPU box (`VM_IP`, `VM_SSH_KEY`, …). `.env`
is gitignored — never commit real secrets.

## Run (back-to-back, one terminal each)

```bash
scripts/db-setup.sh          # 0) apply DB SQL (add --schema the first time)
scripts/start-vm.sh          # 1) push + build (first time) + start the renderer; blocks until warm
scripts/start-tunnel.sh      # 2) new terminal: localhost:8004 -> VM renderer (keep open)
scripts/start-frontend.sh    # 3) new terminal: http://localhost:5173 (keep open)
```

Then open **http://localhost:5173** → **Add story** (.txt/.pdf) or open one →
highlight → **Query** → **Generate**. Full runbook (overrides, what-runs-where,
manual start): [`_docs/DEPLOY.md`](_docs/DEPLOY.md).

### Using the studio
- **Output mode** (Real-time vs Finished): real-time streams frames live and shows
  a steer box; finished hides the frames and reveals only the completed mp4.
- **Steer** (real-time): type a change ("make it snow") and the frames morph toward
  it; stop typing and they hold steady.

## Database

`scripts/db-setup.sh` applies the SQL with `psql` using `BVG_DATABASE_URL` from
`.env` (pooler URL, `sslmode=require`):

- `schema.sql` — Tier 1 (characters, locations), Tier 2 (temporal state deltas),
  Tier 3 (paragraph beats). `CREATE TABLE` — first time only (`--schema`).
- `rpc_resolve_contexts.sql` — `resolve_contexts(paragraph_ids[])` +
  `delete_book(book_id)` SECURITY DEFINER functions, plus anon RLS read
  policies/grants. Idempotent.
- `migration_cascade_fix.sql` — re-asserts every FK's delete behaviour so deleting
  a book removes all its rows. Idempotent.

Verify the RPC:
```bash
curl -s -X POST "$VITE_SUPABASE_URL/rest/v1/rpc/resolve_contexts" \
  -H "apikey: $ANON_KEY" -H "Authorization: Bearer $ANON_KEY" \
  -H "Content-Type: application/json" -d '{"p_paragraph_ids":[5]}'
```

## Frontend (dev notes)

React + TypeScript + Vite (`@vitejs/plugin-react`, Tailwind v4). `scripts/start-frontend.sh`
runs `npm install` (first run) then `npm run dev`. Type-check with
`cd frontend && npx tsc --noEmit`.
