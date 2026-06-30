# Deploy & run

The whole stack: a GPU VM running the renderer (`renderer`), Supabase
for data, and the Vite frontend on your laptop.

## One command (fresh GPU instance)

```bash
cp scripts/deploy.config.example scripts/deploy.config   # fill in once (see .env.example block 3)
scripts/deploy.sh <VM_IP>
```

`deploy.sh` is idempotent and does everything end-to-end:
1. probe the VM,
2. rsync the repo + write the VM's `~/shotbook/.env`,
3. `resume_vm.sh` (build venvs + flash-attn + Wan2.1-1.3B weights) — skipped if already built,
4. start the renderer on `:8004` (tmux `sb`, model warm),
5. open the `localhost:8004` SSH tunnel and start the frontend (`http://localhost:5173`).

Only the IP changes per instance; the key path + secrets live in
`scripts/deploy.config` (gitignored).

## What runs where
- **VM** (`tmux sb`): `renderer.main:app` on `:8004` — shot planning
  (Claude) + streaming render + `/ingest`. This is the only server-side process.
- **Laptop**: the SSH tunnel (`localhost:8004 → VM:8004`) and the Vite dev server.
- **Supabase**: data + the `resolve_contexts` RPC (frontend calls it directly).

## Manual VM start (if not using deploy.sh)
```bash
ssh <user>@<vm-ip> -i private_key.pem
cd ~/shotbook && set -a; . ./.env; set +a
CUDA_VISIBLE_DEVICES=0 .venv-renderer/bin/uvicorn renderer.main:app --host 0.0.0.0 --port 8004
```

## Ingest a story
- **From the UI** (preferred): Library → **Add story** → upload a `.txt`/`.pdf`.
- **CLI** (on the VM): `set -a; . ./.env; set +a` then
  `PYTHONPATH=. .venv-api/bin/python -m ingestion.orchestrator corpus/<book>.txt --title '...' --author '...'`

## Stop
```bash
pkill -f "8004:localhost:8004"   # tunnel
pkill -f vite                    # frontend
# terminate the GPU instance to stop spend
```
