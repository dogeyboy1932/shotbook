-- =============================================================================
-- book_video_gen :: 3-Tier World-State Schema
--
-- Tier 1 (Global Registry):  characters, locations
--   Immutable baseline visual/vocal identity for every entity in the book.
--
-- Tier 2 (Temporal Ledger):  character_states, location_states
--   Append-only delta log. Each row is valid over a half-open paragraph range
--   [valid_from_paragraph_id, valid_until_paragraph_id). A NULL
--   valid_until_paragraph_id means the delta is still in effect at the end
--   of the book (the "current" state).
--
-- Tier 3 (Paragraph Beats):  paragraphs, paragraph_characters
--   One row per paragraph: exact text, active entities, camera framing,
--   dialogue, and SFX cues needed to render a self-contained video clip.
--
-- Design note: paragraphs.sequence_index (not paragraph_id) is the timeline
-- axis. paragraph_id is a stable surrogate key assigned at insert time;
-- because paragraphs are always bulk-inserted in book order, paragraph_id is
-- monotonically increasing with sequence_index, but all temporal-range
-- comparisons below explicitly join through sequence_index to remain correct
-- even if paragraphs are ever re-ingested or re-ordered.
-- =============================================================================

BEGIN;

CREATE EXTENSION IF NOT EXISTS pgcrypto;

-- -----------------------------------------------------------------------------
-- books :: one row per ingested book. Root of every other table.
-- -----------------------------------------------------------------------------
CREATE TABLE books (
    book_id         BIGSERIAL PRIMARY KEY,
    title           TEXT NOT NULL,
    author          TEXT,
    source_uri      TEXT,                       -- original file path / S3 key the book was ingested from
    ingestion_status TEXT NOT NULL DEFAULT 'pending'
                        CHECK (ingestion_status IN ('pending', 'registry_pass_complete', 'beats_pass_complete', 'failed')),
    created_at      TIMESTAMPTZ NOT NULL DEFAULT now()
);

-- =============================================================================
-- TIER 1 :: GLOBAL REGISTRY
-- =============================================================================

CREATE TABLE characters (
    character_id                BIGSERIAL PRIMARY KEY,
    book_id                     BIGINT NOT NULL REFERENCES books(book_id) ON DELETE CASCADE,
    canonical_name              TEXT NOT NULL,
    aliases                     TEXT[] NOT NULL DEFAULT '{}',   -- alternate names/nicknames used for entity resolution during ingestion
    baseline_visual_description TEXT NOT NULL,                  -- immutable physical description fed to the video diffusion model (face, build, base wardrobe)
    baseline_voice_description  TEXT NOT NULL,                  -- text vocal prompt (timbre, accent, cadence) used when no cloned reference audio exists
    voice_reference_audio_uri   TEXT,                           -- optional path/URL to a clean speech sample for XTTS voice cloning
    extended_profile             JSONB NOT NULL DEFAULT '{}'::jsonb,
        -- immutable baseline narrative profile: backstory, personality_traits (list),
        -- speech_patterns, motivations, relationships ({other canonical_name: description}).
        -- JSONB (not fixed columns) so new narrative attributes don't require a migration.
    created_at                  TIMESTAMPTZ NOT NULL DEFAULT now(),

    CONSTRAINT uq_characters_book_name UNIQUE (book_id, canonical_name)
);

CREATE INDEX idx_characters_book_id ON characters (book_id);

CREATE TABLE locations (
    location_id                  BIGSERIAL PRIMARY KEY,
    book_id                      BIGINT NOT NULL REFERENCES books(book_id) ON DELETE CASCADE,
    canonical_name                TEXT NOT NULL,
    aliases                       TEXT[] NOT NULL DEFAULT '{}',
    baseline_visual_description   TEXT NOT NULL,                -- immutable establishing-shot description (architecture, terrain, palette)
    baseline_ambient_sfx_prompt   TEXT NOT NULL,                 -- default Stable Audio prompt for this location's ambience when no delta is active
    extended_profile               JSONB NOT NULL DEFAULT '{}'::jsonb,
        -- immutable baseline narrative profile: history, narrative_significance.
    created_at                    TIMESTAMPTZ NOT NULL DEFAULT now(),

    CONSTRAINT uq_locations_book_name UNIQUE (book_id, canonical_name)
);

CREATE INDEX idx_locations_book_id ON locations (book_id);

-- =============================================================================
-- TIER 3 :: PARAGRAPH BEATS
-- (created before Tier 2 so Tier 2's FKs into paragraphs can be declared)
-- =============================================================================

CREATE TABLE paragraphs (
    paragraph_id        BIGSERIAL PRIMARY KEY,
    book_id              BIGINT NOT NULL REFERENCES books(book_id) ON DELETE CASCADE,
    chapter_number        INTEGER NOT NULL,
    sequence_index         INTEGER NOT NULL,        -- global monotonic order of this paragraph within the book; THE timeline axis
    raw_text               TEXT NOT NULL,
    active_location_id      BIGINT REFERENCES locations(location_id) ON DELETE SET NULL,
    camera_framing           TEXT NOT NULL DEFAULT 'medium_shot'
                                CHECK (camera_framing IN (
                                    'extreme_close_up', 'close_up', 'medium_shot',
                                    'wide_shot', 'establishing_shot', 'over_the_shoulder', 'pov'
                                )),
    action_summary            TEXT NOT NULL,          -- one-line description of the physical action/beat, fed directly to the video diffusion prompt
    dialogue_script            JSONB NOT NULL DEFAULT '[]'::jsonb,
        -- [{ "character_id": int, "line": str, "emotion": str, "delivery": str }, ...]
    sfx_prompts                 JSONB NOT NULL DEFAULT '[]'::jsonb,
        -- ["distant thunder rumble", "wooden floorboard creak", ...] discrete Stable Audio cues for this beat
    created_at                   TIMESTAMPTZ NOT NULL DEFAULT now(),

    CONSTRAINT uq_paragraphs_book_sequence UNIQUE (book_id, sequence_index),
    CONSTRAINT chk_dialogue_script_is_array CHECK (jsonb_typeof(dialogue_script) = 'array'),
    CONSTRAINT chk_sfx_prompts_is_array CHECK (jsonb_typeof(sfx_prompts) = 'array')
);

-- Sequential scan/range queries (e.g. "give me paragraphs 100-150 of book 4") and
-- the Tier 2 temporal joins below both hinge on this index.
CREATE INDEX idx_paragraphs_book_sequence ON paragraphs (book_id, sequence_index);
CREATE INDEX idx_paragraphs_active_location ON paragraphs (active_location_id);

-- Many-to-many: which characters are physically present/speaking in a paragraph.
CREATE TABLE paragraph_characters (
    paragraph_id    BIGINT NOT NULL REFERENCES paragraphs(paragraph_id) ON DELETE CASCADE,
    character_id     BIGINT NOT NULL REFERENCES characters(character_id) ON DELETE CASCADE,

    PRIMARY KEY (paragraph_id, character_id)
);

CREATE INDEX idx_paragraph_characters_character_id ON paragraph_characters (character_id);

-- =============================================================================
-- TIER 2 :: TEMPORAL LEDGER
-- =============================================================================

CREATE TABLE character_states (
    state_id                  BIGSERIAL PRIMARY KEY,
    character_id                BIGINT NOT NULL REFERENCES characters(character_id) ON DELETE CASCADE,
    valid_from_paragraph_id      BIGINT NOT NULL REFERENCES paragraphs(paragraph_id) ON DELETE CASCADE,
    valid_until_paragraph_id      BIGINT REFERENCES paragraphs(paragraph_id) ON DELETE CASCADE,
    appearance_delta                TEXT,            -- e.g. "wearing a torn cloak, fresh cut above left eye"; NULL = no visual change from prior state
    emotional_state                  TEXT,            -- e.g. "terrified, breathing hard"
    vocal_delta_prompt                 TEXT,          -- e.g. "raspy, out of breath, whispering"; NULL = baseline voice still applies
    profile_snapshot                     JSONB,        -- full current narrative profile (same shape as characters.extended_profile);
                                                        -- NULL until the first profile-affecting change, then carries forward like the columns above
    created_at                          TIMESTAMPTZ NOT NULL DEFAULT now(),

    CONSTRAINT chk_character_state_range CHECK (
        valid_until_paragraph_id IS NULL OR valid_until_paragraph_id <> valid_from_paragraph_id
    )
);

-- Critical hot-path index: "find the most recent state for character X whose
-- valid_from is <= the target paragraph" -> ORDER BY valid_from_paragraph_id DESC LIMIT 1.
CREATE INDEX idx_character_states_lookup
    ON character_states (character_id, valid_from_paragraph_id DESC);
CREATE INDEX idx_character_states_valid_until
    ON character_states (valid_until_paragraph_id);

CREATE TABLE location_states (
    state_id                BIGSERIAL PRIMARY KEY,
    location_id                BIGINT NOT NULL REFERENCES locations(location_id) ON DELETE CASCADE,
    valid_from_paragraph_id      BIGINT NOT NULL REFERENCES paragraphs(paragraph_id) ON DELETE CASCADE,
    valid_until_paragraph_id      BIGINT REFERENCES paragraphs(paragraph_id) ON DELETE CASCADE,
    atmosphere_delta                TEXT,            -- e.g. "rain-soaked, fog rolling in"
    lighting_state                    TEXT,           -- e.g. "lit only by a guttering torch"
    ambient_sfx_delta                   TEXT,         -- overrides baseline_ambient_sfx_prompt while this state is active
    profile_snapshot                      JSONB,       -- full current narrative profile (same shape as locations.extended_profile);
                                                        -- NULL until the first profile-affecting change, then carries forward like the columns above
    created_at                           TIMESTAMPTZ NOT NULL DEFAULT now(),

    CONSTRAINT chk_location_state_range CHECK (
        valid_until_paragraph_id IS NULL OR valid_until_paragraph_id <> valid_from_paragraph_id
    )
);

CREATE INDEX idx_location_states_lookup
    ON location_states (location_id, valid_from_paragraph_id DESC);
CREATE INDEX idx_location_states_valid_until
    ON location_states (valid_until_paragraph_id);

COMMIT;
