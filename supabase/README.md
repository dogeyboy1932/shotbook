# Supabase (database)

The 3-tier world-state schema plus the RPC the frontend calls to resolve a
highlighted span. Apply both once against the project's Postgres.

## Files
- `schema.sql` — Tier 1 (characters, locations), Tier 2 (temporal state deltas),
  Tier 3 (paragraph beats). See `docs/ARCHITECTURE.md` for the model.
- `rpc_resolve_contexts.sql` — `resolve_contexts(paragraph_ids[])` SECURITY DEFINER
  function (resolved Tier-1 + latest Tier-2 state as JSON) + anon RLS read
  policies/grants for `books` and `paragraphs`.

## Apply

```bash
# Use the pooler connection string (sslmode=require). PW from your secrets.
PGURL='postgresql://postgres.<ref>:<password>@aws-1-us-east-1.pooler.supabase.com:5432/postgres?sslmode=require'

psql "$PGURL" -v ON_ERROR_STOP=1 -f supabase/schema.sql            # first time only
psql "$PGURL" -v ON_ERROR_STOP=1 -f supabase/rpc_resolve_contexts.sql
```

`rpc_resolve_contexts.sql` is idempotent (CREATE OR REPLACE + DROP POLICY IF
EXISTS), so re-running it to update the RPC/policies is safe.

## Verify
```bash
curl -s -X POST "$SUPABASE_URL/rest/v1/rpc/resolve_contexts" \
  -H "apikey: $ANON_KEY" -H "Authorization: Bearer $ANON_KEY" \
  -H "Content-Type: application/json" -d '{"p_paragraph_ids":[5]}'
```
