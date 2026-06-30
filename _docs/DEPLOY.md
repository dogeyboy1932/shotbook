# Deploy & run

The whole stack: a GPU VM running the renderer (`renderer/`), Supabase for data,
and the Vite frontend on your laptop. **All configuration lives in one file: the
repo-root `.env`** (copy `.env.example` → `.env` and fill it in, including
`VM_IP`). Nothing takes command-line config except an optional IP override.

## Run it (back-to-back commands, one terminal each)

```bash
# 0) Database — once, or whenever the SQL changes (idempotent RPC + cascades).
scripts/db-setup.sh            # add --schema the very first time to CREATE the tables

# 1) GPU VM — push code, build the env (first time), start the renderer. Blocks
#    until the model is warm (~90s once built), then returns.
scripts/start-vm.sh

# 2) Tunnel — new terminal; holds localhost:8004 -> VM renderer. Keep open.
scripts/start-tunnel.sh

# 3) Frontend — new terminal; Vite on http://localhost:5173. Keep open.
scripts/start-frontend.sh
```

Then open **http://localhost:5173** → **Add story** (.txt/.pdf) or open one →
highlight → **Query** → **Generate**.

Each script reads everything from `.env`. Pass an IP to override the box for a
one-off run, e.g. `scripts/start-vm.sh 203.0.113.5` (or `scripts/start-tunnel.sh 203.0.113.5`).

`scripts/start-vm.sh` is idempotent: the heavy rebuild (venvs + flash-attn +
Wan2.1-1.3B weights via `scripts/resume_vm.sh`) is skipped if the box is already
built, so re-running just restarts the renderer.

## What runs where
- **VM** (`tmux sb`): `renderer.main:app` on `:8004` — shot planning (Claude) +
  streaming render + `/ingest`. The only server-side process.
- **Laptop**: the SSH tunnel (`localhost:8004 → VM:8004`) and the Vite dev server.
- **Supabase**: data + the `resolve_contexts` / `delete_book` RPCs (the frontend
  calls them directly).

## Manual VM start (without start-vm.sh)
```bash
ssh <user>@<vm-ip> -i private_key.pem
cd ~/shotbook && set -a; . ./.env; set +a
CUDA_VISIBLE_DEVICES=0 .venv-renderer/bin/uvicorn renderer.main:app --host 0.0.0.0 --port 8004
```

## Ingest a story
- **From the UI** (preferred): Library → **Add story** → upload a `.txt`/`.pdf`.
- **CLI** (on the VM): `set -a; . ./.env; set +a` then
  `PYTHONPATH=. .venv-api/bin/python -m ingestion.orchestrator example_corpus/<book>.txt --title '...' --author '...'`

## Stop
```bash
# Ctrl-C the tunnel and frontend terminals. The VM renderer stays in tmux 'sb';
# terminate the GPU instance to stop spend.
```
