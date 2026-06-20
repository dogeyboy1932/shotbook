"""Shared logic for compiling Tier 1 + Tier 2 state into generation payloads.

Used by both the single-paragraph endpoint (`GET /api/generate-context/{id}`)
and the batch endpoint (`POST /api/generate-context/batch`) so the temporal
resolution SQL and row->payload mapping live in exactly one place.
"""
from __future__ import annotations

import logging

from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

from app.schemas import (
    CharacterContextPayload,
    DialogueLinePayload,
    GenerationContextPayload,
    LocationContextPayload,
)

logger = logging.getLogger("app.context_compiler")

# Identical temporal-resolution logic to the original single-paragraph query,
# generalized to resolve every paragraph_id in :paragraph_ids in one round trip.
_COMPILE_QUERY = text(
    """
    WITH target AS (
        SELECT paragraph_id, book_id, sequence_index, chapter_number, raw_text,
               camera_framing, action_summary, dialogue_script, sfx_prompts,
               active_location_id
        FROM paragraphs
        WHERE paragraph_id = ANY(:paragraph_ids)
    )
    SELECT
        t.paragraph_id,
        t.book_id,
        t.sequence_index,
        t.chapter_number,
        t.raw_text,
        t.camera_framing,
        t.action_summary,
        t.dialogue_script,
        t.sfx_prompts,
        COALESCE(
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
        ) AS characters_json,
        (
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
        ) AS location_json
    FROM target t
    ORDER BY t.sequence_index
    """
)


def compose_narrative_prompt(
    characters: list[CharacterContextPayload],
    location: LocationContextPayload | None,
    action_summary: str,
) -> str:
    """Flattens the structured story-state into one prose block. Diffusion
    video models take a text prompt, not a JSON tree -- this is the piece
    that actually turns "story state at sequence_index N" into something
    you can hand to one."""
    lines: list[str] = []

    if location is not None:
        location_line = f"Setting: {location.name} -- {location.visual_description}"
        history = location.profile.get("history")
        if history:
            location_line += f" ({history})"
        lines.append(location_line)

    for character in characters:
        profile = character.profile
        character_line = f"{character.name}: {character.visual_description}"
        if character.emotional_state:
            character_line += f", currently {character.emotional_state}"
        personality_bits = profile.get("personality_traits") or []
        if personality_bits:
            character_line += f"; personality: {', '.join(personality_bits)}"
        personality_note = profile.get("personality_note")
        if personality_note:
            character_line += f" ({personality_note})"
        relationships = profile.get("relationships") or {}
        if relationships:
            relationship_bits = "; ".join(f"{name}: {desc}" for name, desc in relationships.items())
            character_line += f"; relationships -- {relationship_bits}"
        lines.append(character_line)

    lines.append(f"Action: {action_summary}")
    return " | ".join(lines)


def _row_to_payload(row: dict, logger_paragraph_id: int) -> GenerationContextPayload:
    characters = [
        CharacterContextPayload(
            character_id=c["character_id"],
            name=c["name"],
            visual_description=c["visual_description"],
            voice_description=c["voice_description"],
            voice_reference_audio_uri=c["voice_reference_audio_uri"],
            emotional_state=c["emotional_state"],
            profile=c["profile"] or {},
        )
        for c in row["characters_json"]
    ]
    character_name_by_id = {c.character_id: c.name for c in characters}

    location_json = row["location_json"]
    location = (
        LocationContextPayload(
            location_id=location_json["location_id"],
            name=location_json["name"],
            visual_description=location_json["visual_description"],
            lighting_state=location_json["lighting_state"],
            ambient_sfx_prompt=location_json["ambient_sfx_prompt"],
            profile=location_json["profile"] or {},
        )
        if location_json is not None
        else None
    )

    dialogue_script = []
    for entry in row["dialogue_script"]:
        character_id = entry["character_id"]
        character_name = character_name_by_id.get(character_id)
        if character_name is None:
            logger.warning(
                "Dialogue in paragraph_id=%d references character_id=%d not present "
                "in this paragraph's active-character list; using a placeholder name",
                logger_paragraph_id,
                character_id,
            )
            character_name = "Unknown"
        dialogue_script.append(
            DialogueLinePayload(
                character_id=character_id,
                character_name=character_name,
                line=entry["line"],
                emotion=entry["emotion"],
                delivery=entry["delivery"],
            )
        )

    return GenerationContextPayload(
        paragraph_id=row["paragraph_id"],
        book_id=row["book_id"],
        sequence_index=row["sequence_index"],
        chapter_number=row["chapter_number"],
        raw_text=row["raw_text"],
        camera_framing=row["camera_framing"],
        action_summary=row["action_summary"],
        characters=characters,
        location=location,
        dialogue_script=dialogue_script,
        sfx_prompts=list(row["sfx_prompts"]),
        narrative_context=compose_narrative_prompt(characters, location, row["action_summary"]),
    )


async def compile_contexts(
    session: AsyncSession, paragraph_ids: list[int]
) -> list[GenerationContextPayload]:
    """Resolves Tier 1 baseline + latest Tier 2 delta for every paragraph in
    `paragraph_ids`, in one round trip. Returns payloads ordered by
    sequence_index. Paragraph ids that don't exist are silently omitted."""
    if not paragraph_ids:
        return []

    result = await session.execute(_COMPILE_QUERY, {"paragraph_ids": paragraph_ids})
    rows = result.mappings().all()
    return [_row_to_payload(row, row["paragraph_id"]) for row in rows]
