-- =============================================================================
-- resolve_contexts(paragraph_ids) :: Supabase RPC
--
-- Ports app/context_compiler.py::_COMPILE_QUERY into a Postgres function so the
-- React frontend can resolve a highlighted span's full Tier-1 + latest Tier-2
-- state directly from Supabase (POST /rest/v1/rpc/resolve_contexts) -- no
-- FastAPI middle tier.
--
-- Returns a JSON array of per-paragraph context objects ordered by
-- sequence_index, each shaped like the old GenerationContextPayload (minus the
-- prose `narrative_context`, which the UI now renders from structured fields):
--   { paragraph_id, book_id, sequence_index, chapter_number, raw_text,
--     camera_framing, action_summary, characters[], location|null,
--     dialogue_script[], sfx_prompts[] }
--
-- dialogue_script entries are resolved to include character_name (the raw
-- column stores character_id only), matching what compose_scene expects.
--
-- SECURITY DEFINER so the anon role can call it without direct SELECT on the
-- Tier-1/Tier-2 tables; the temporal carry-forward logic is identical to the
-- LATERAL join the Python query used.
-- =============================================================================

CREATE OR REPLACE FUNCTION public.resolve_contexts(p_paragraph_ids bigint[])
RETURNS json
LANGUAGE sql
STABLE
SECURITY DEFINER
SET search_path = public
AS $$
  SELECT COALESCE(json_agg(obj ORDER BY seq), '[]'::json)
  FROM (
    SELECT
      t.sequence_index AS seq,
      json_build_object(
        'paragraph_id',   t.paragraph_id,
        'book_id',        t.book_id,
        'sequence_index', t.sequence_index,
        'chapter_number', t.chapter_number,
        'raw_text',       t.raw_text,
        'camera_framing', t.camera_framing,
        'action_summary', t.action_summary,
        'characters', COALESCE(
          (
            SELECT json_agg(
              json_build_object(
                'character_id', c.character_id,
                'name', c.canonical_name,
                'visual_description', CASE WHEN cs.appearance_delta IS NOT NULL
                    THEN c.baseline_visual_description || '; currently: ' || cs.appearance_delta
                    ELSE c.baseline_visual_description END,
                'voice_description', COALESCE(cs.vocal_delta_prompt, c.baseline_voice_description),
                'voice_reference_audio_uri', c.voice_reference_audio_uri,
                'emotional_state', cs.emotional_state,
                'profile', COALESCE(cs.profile_snapshot, c.extended_profile)
              )
            )
            FROM paragraph_characters pc
            JOIN characters c ON c.character_id = pc.character_id
            LEFT JOIN LATERAL (
              SELECT cs2.appearance_delta, cs2.emotional_state, cs2.vocal_delta_prompt, cs2.profile_snapshot
              FROM character_states cs2
              JOIN paragraphs pf ON pf.paragraph_id = cs2.valid_from_paragraph_id
              LEFT JOIN paragraphs pu ON pu.paragraph_id = cs2.valid_until_paragraph_id
              WHERE cs2.character_id = c.character_id
                AND pf.sequence_index <= t.sequence_index
                AND (cs2.valid_until_paragraph_id IS NULL OR pu.sequence_index > t.sequence_index)
              ORDER BY pf.sequence_index DESC
              LIMIT 1
            ) cs ON TRUE
            WHERE pc.paragraph_id = t.paragraph_id
          ),
          '[]'::json
        ),
        'location', (
          SELECT json_build_object(
            'location_id', l.location_id,
            'name', l.canonical_name,
            'visual_description', CASE WHEN ls.atmosphere_delta IS NOT NULL
                THEN l.baseline_visual_description || '; atmosphere: ' || ls.atmosphere_delta
                ELSE l.baseline_visual_description END,
            'lighting_state', ls.lighting_state,
            'ambient_sfx_prompt', COALESCE(ls.ambient_sfx_delta, l.baseline_ambient_sfx_prompt),
            'profile', COALESCE(ls.profile_snapshot, l.extended_profile)
          )
          FROM locations l
          LEFT JOIN LATERAL (
            SELECT ls2.atmosphere_delta, ls2.lighting_state, ls2.ambient_sfx_delta, ls2.profile_snapshot
            FROM location_states ls2
            JOIN paragraphs pf ON pf.paragraph_id = ls2.valid_from_paragraph_id
            LEFT JOIN paragraphs pu ON pu.paragraph_id = ls2.valid_until_paragraph_id
            WHERE ls2.location_id = l.location_id
              AND pf.sequence_index <= t.sequence_index
              AND (ls2.valid_until_paragraph_id IS NULL OR pu.sequence_index > t.sequence_index)
            ORDER BY pf.sequence_index DESC
            LIMIT 1
          ) ls ON TRUE
          WHERE l.location_id = t.active_location_id
        ),
        'dialogue_script', COALESCE(
          (
            SELECT json_agg(
              json_build_object(
                'character_id', (d->>'character_id')::bigint,
                'character_name', COALESCE(dc.canonical_name, 'Unknown'),
                'line', d->>'line',
                'emotion', d->>'emotion',
                'delivery', d->>'delivery'
              )
              ORDER BY ord
            )
            FROM jsonb_array_elements(t.dialogue_script) WITH ORDINALITY AS arr(d, ord)
            LEFT JOIN characters dc ON dc.character_id = (d->>'character_id')::bigint
          ),
          '[]'::json
        ),
        'sfx_prompts', t.sfx_prompts
      ) AS obj
    FROM paragraphs t
    WHERE t.paragraph_id = ANY(p_paragraph_ids)
  ) s;
$$;

-- Let the public (anon) and signed-in roles call the RPC.
GRANT EXECUTE ON FUNCTION public.resolve_contexts(bigint[]) TO anon, authenticated;

-- -----------------------------------------------------------------------------
-- Direct REST reads the frontend needs: books + paragraphs (api.ts uses
-- /rest/v1/books and /rest/v1/paragraphs). Enable RLS with read-only policies
-- and grant SELECT to the anon role. Tier-1/Tier-2 tables are intentionally NOT
-- exposed -- they're only reachable through the SECURITY DEFINER RPC above.
-- Writes (ingestion) use the service_role/postgres connection, which bypasses RLS.
-- -----------------------------------------------------------------------------
ALTER TABLE public.books ENABLE ROW LEVEL SECURITY;
ALTER TABLE public.paragraphs ENABLE ROW LEVEL SECURITY;

DROP POLICY IF EXISTS books_anon_read ON public.books;
CREATE POLICY books_anon_read ON public.books FOR SELECT TO anon, authenticated USING (true);

DROP POLICY IF EXISTS paragraphs_anon_read ON public.paragraphs;
CREATE POLICY paragraphs_anon_read ON public.paragraphs FOR SELECT TO anon, authenticated USING (true);

GRANT USAGE ON SCHEMA public TO anon, authenticated;
GRANT SELECT ON public.books, public.paragraphs TO anon, authenticated;
