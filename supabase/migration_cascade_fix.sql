-- =============================================================================
-- Migration: assert robust delete-cascades across the whole schema (#6).
--
-- Goal: deleting a book removes EVERY row that belongs to it (characters,
-- locations, paragraphs, the M2M link table, and both temporal state ledgers),
-- and deleting a location no longer blocks on paragraphs that point at it.
--
-- Idempotent: each FK is dropped (IF EXISTS) and re-added with the intended
-- ON DELETE action, so this can be re-applied safely on a live DB that predates
-- the cascade definitions in schema.sql.
--
-- Apply (pooler, TLS required):
--   psql "$BVG_DATABASE_URL?sslmode=require" -f supabase/migration_cascade_fix.sql
-- (BVG_DATABASE_URL is the postgresql:// URL; strip any +asyncpg driver suffix.)
-- =============================================================================

BEGIN;

-- Tier 1 -> books (CASCADE: a book's whole cast/world goes with it)
ALTER TABLE characters DROP CONSTRAINT IF EXISTS characters_book_id_fkey;
ALTER TABLE characters
    ADD CONSTRAINT characters_book_id_fkey
    FOREIGN KEY (book_id) REFERENCES books(book_id) ON DELETE CASCADE;

ALTER TABLE locations DROP CONSTRAINT IF EXISTS locations_book_id_fkey;
ALTER TABLE locations
    ADD CONSTRAINT locations_book_id_fkey
    FOREIGN KEY (book_id) REFERENCES books(book_id) ON DELETE CASCADE;

-- Tier 3 paragraphs -> books (CASCADE) and -> locations (SET NULL so deleting a
-- single location doesn't fail on paragraphs that referenced it).
ALTER TABLE paragraphs DROP CONSTRAINT IF EXISTS paragraphs_book_id_fkey;
ALTER TABLE paragraphs
    ADD CONSTRAINT paragraphs_book_id_fkey
    FOREIGN KEY (book_id) REFERENCES books(book_id) ON DELETE CASCADE;

ALTER TABLE paragraphs DROP CONSTRAINT IF EXISTS paragraphs_active_location_id_fkey;
ALTER TABLE paragraphs
    ADD CONSTRAINT paragraphs_active_location_id_fkey
    FOREIGN KEY (active_location_id) REFERENCES locations(location_id) ON DELETE SET NULL;

-- M2M link table -> paragraphs / characters (CASCADE)
ALTER TABLE paragraph_characters DROP CONSTRAINT IF EXISTS paragraph_characters_paragraph_id_fkey;
ALTER TABLE paragraph_characters
    ADD CONSTRAINT paragraph_characters_paragraph_id_fkey
    FOREIGN KEY (paragraph_id) REFERENCES paragraphs(paragraph_id) ON DELETE CASCADE;

ALTER TABLE paragraph_characters DROP CONSTRAINT IF EXISTS paragraph_characters_character_id_fkey;
ALTER TABLE paragraph_characters
    ADD CONSTRAINT paragraph_characters_character_id_fkey
    FOREIGN KEY (character_id) REFERENCES characters(character_id) ON DELETE CASCADE;

-- Tier 2 character_states -> characters / paragraphs (CASCADE)
ALTER TABLE character_states DROP CONSTRAINT IF EXISTS character_states_character_id_fkey;
ALTER TABLE character_states
    ADD CONSTRAINT character_states_character_id_fkey
    FOREIGN KEY (character_id) REFERENCES characters(character_id) ON DELETE CASCADE;

ALTER TABLE character_states DROP CONSTRAINT IF EXISTS character_states_valid_from_paragraph_id_fkey;
ALTER TABLE character_states
    ADD CONSTRAINT character_states_valid_from_paragraph_id_fkey
    FOREIGN KEY (valid_from_paragraph_id) REFERENCES paragraphs(paragraph_id) ON DELETE CASCADE;

ALTER TABLE character_states DROP CONSTRAINT IF EXISTS character_states_valid_until_paragraph_id_fkey;
ALTER TABLE character_states
    ADD CONSTRAINT character_states_valid_until_paragraph_id_fkey
    FOREIGN KEY (valid_until_paragraph_id) REFERENCES paragraphs(paragraph_id) ON DELETE CASCADE;

-- Tier 2 location_states -> locations / paragraphs (CASCADE)
ALTER TABLE location_states DROP CONSTRAINT IF EXISTS location_states_location_id_fkey;
ALTER TABLE location_states
    ADD CONSTRAINT location_states_location_id_fkey
    FOREIGN KEY (location_id) REFERENCES locations(location_id) ON DELETE CASCADE;

ALTER TABLE location_states DROP CONSTRAINT IF EXISTS location_states_valid_from_paragraph_id_fkey;
ALTER TABLE location_states
    ADD CONSTRAINT location_states_valid_from_paragraph_id_fkey
    FOREIGN KEY (valid_from_paragraph_id) REFERENCES paragraphs(paragraph_id) ON DELETE CASCADE;

ALTER TABLE location_states DROP CONSTRAINT IF EXISTS location_states_valid_until_paragraph_id_fkey;
ALTER TABLE location_states
    ADD CONSTRAINT location_states_valid_until_paragraph_id_fkey
    FOREIGN KEY (valid_until_paragraph_id) REFERENCES paragraphs(paragraph_id) ON DELETE CASCADE;

COMMIT;
