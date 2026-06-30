#!/usr/bin/env bash
# [0] Apply the database SQL to Supabase (run once / when SQL changes). Uses
# BVG_DATABASE_URL from .env (pooler URL), forcing sslmode=require.
#
#   scripts/db-setup.sh            # apply the idempotent RPC + cascade migration
#   scripts/db-setup.sh --schema   # ALSO create the tables (first-time-only)
#
# Order matters: schema (tables) -> rpc (resolve_contexts + grants) -> migration
# (re-assert delete cascades). The RPC and migration are idempotent and safe to
# re-run; --schema runs CREATE TABLE and is meant for a fresh database only.
set -euo pipefail
source "$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)/_config.sh"

RAW="$(cfg BVG_DATABASE_URL)"
[ -n "$RAW" ] || { echo "!! BVG_DATABASE_URL not set in $CONFIG"; exit 1; }
command -v psql >/dev/null || { echo "!! psql not found -- install the postgresql client"; exit 1; }

# psql wants postgresql:// (not the SQLAlchemy +asyncpg driver) and TLS on.
URL="${RAW/+asyncpg/}"
case "$URL" in *\?*) URL="${URL}&sslmode=require";; *) URL="${URL}?sslmode=require";; esac

run(){ say "applying $1"; psql "$URL" -v ON_ERROR_STOP=1 -f "$REPO/$1"; }

[ "${1:-}" = "--schema" ] && run supabase/schema.sql
run supabase/rpc_resolve_contexts.sql
run supabase/migration_cascade_fix.sql
say "database ready"
