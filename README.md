# ShotBook

Highlight a passage in a book → get a cinematic video grounded in the book's
resolved world-state, generated **live, frame by frame**.

A reader selects text; the app resolves the exact character/location state at
that point in the story and turns it into a continuous, seamless video clip on a
GPU — streamed to the browser as it generates.

## Architecture (one diagram)

```
React frontend ──► Supabase           books + paragraphs, and resolve_contexts() RPC
   (frontend/)  └─► VM renderer        POST /generate (plan + render), live MJPEG, /ingest
                     (renderer, one GPU process)
                       ├─ plan   : Claude shot breakdown grounded in world-state
                       └─ render : Wan2.1-1.3B streaming model, one seamless rollout

Ingestion (ingestion/, Claude → Supabase): two passes write the 3-tier world-state.
```

There is **no middle API tier** — the browser talks only to Supabase and the VM.

## Repository layout

| Path | What |
|------|------|
| `frontend/` | React + Vite reader/studio UI |
| `renderer/` | The single VM backend: shot planning (Claude) + streaming render + `/ingest` upload. Holds the vendored Wan engine. |
| `ingestion/` | Two-pass Claude pipeline that writes the world-state to Supabase (run as a script or via the UI). |
| `supabase/` | Database: `schema.sql` (3-tier world-state) + `rpc_resolve_contexts.sql`. |
| `corpus/` | Public-domain source texts for ingestion smoke tests. |
| `scripts/` | `deploy.sh` (one-command bring-up), `resume_vm.sh`, deploy config, and the runbook (`DEPLOY.md`). |
| `_docs/` | Architecture and design notes. |
| `.env.example` | Every config var, documented (see it before deploying). |

## Quickstart

1. **Database** — apply `supabase/` once (see `supabase/README.md`).
2. **GPU** — `scripts/deploy.sh <VM_IP>` brings up the renderer, tunnel, and frontend
   end-to-end. Config lives in `scripts/deploy.config` (see `scripts/DEPLOY.md`).
3. **Use it** — open http://localhost:5173 → **Add story** (.txt/.pdf) or open one →
   highlight → **Query** → **Generate**.

The 3-tier world-state model (immutable baselines + temporal deltas per paragraph)
is documented in `_docs/ARCHITECTURE.md`.
