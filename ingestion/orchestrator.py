"""Two-pass, 8-way data-parallel ingestion pipeline.

Pass 1 (Global Registry):
    The book is split into large overlapping-free chunks, one chunk per LLM
    call, fanned out across all 8 GPU replicas concurrently. Each call asks
    the model "who/where appears in this text and what's their baseline
    appearance/voice?". Results are merged, deduplicated by name/alias, and
    written once to `characters` / `locations` (Tier 1).

Pass 2 (Temporal Ledger + Paragraph Beats):
    The condensed Tier 1 registry (id, name, aliases only -- not full
    descriptions, to keep the prompt small) is injected as system context.
    Paragraphs are grouped into small batches and, again, fanned out
    concurrently across all 8 GPUs to extract camera framing, dialogue,
    SFX, and any character/location state *changes* for that batch.

    LLM calls in Pass 2 run concurrently and out-of-order, but the resulting
    Tier 2 rows must be written in strict book order, because each new state
    row carries forward the unset fields of the previous state (a state row
    always holds the character/location's *full* current attributes, not a
    sparse patch -- this keeps the Tier 2 read-path in app/routers a single
    "take the latest row" lookup with no further backfill needed). So Pass 2
    is split into a `generate` stage (parallel) and an `apply` stage
    (strictly sequential, in-memory, then flushed to Postgres).
"""
from __future__ import annotations

import asyncio
import logging
import re
import sys
from dataclasses import dataclass, field

from tqdm import tqdm

from ingestion.db import db_session_scope
from ingestion.config import settings
from ingestion.models import (
    Book,
    Character,
    CharacterState,
    Location,
    LocationState,
    Paragraph,
    ParagraphCharacter,
)
from ingestion.llm_client import GpuWorkerPool, LLMExtractionError
from ingestion.schemas import (
    CharacterProfileDelta,
    CharacterStateChange,
    LocationProfileDelta,
    LocationStateChange,
    ParagraphBatchExtractionResult,
    ParagraphBeat,
    RegistryExtractionResult,
)

logger = logging.getLogger("ingestion.orchestrator")

_CHAPTER_HEADING_RE = re.compile(r"^\s*chapter\s+\d+", re.IGNORECASE)

REGISTRY_SYSTEM_PROMPT = (
    "You are a literary analyst extracting a character/location registry from a novel "
    "excerpt. Identify every distinct named character and location. For each, provide "
    "a baseline visual description and (for characters) a baseline voice description, "
    "suitable as a generative-AI image/voice prompt -- plus a narrative profile: for "
    "characters, their backstory, personality traits, speech patterns, current "
    "motivations, and relationships to other named characters; for locations, their "
    "history and narrative significance. Only include entities clearly established in "
    "this excerpt. Respond with JSON matching the provided schema only."
)

BEATS_SYSTEM_PROMPT_TEMPLATE = (
    "You are breaking a novel into cinematic beats, one per paragraph, for an AI video "
    "generation pipeline. You will be given a numbered list of consecutive paragraphs "
    "and the book's known character/location registry below. For each paragraph, "
    "identify which registry entities are active, the camera framing, a one-line action "
    "summary, any dialogue, and discrete sound-effect prompts. Only emit a "
    "character_state_changes or location_state_changes entry for an entity if THIS "
    "paragraph changes their appearance, emotional state, voice, atmosphere, lighting, "
    "personality, motivation, or a relationship -- omit entities whose state is "
    "unchanged. atmosphere_delta/lighting_state and profile_delta are independent and "
    "both matter: atmosphere_delta/lighting_state track the immediate visual/sensory "
    "mood of the scene right now (e.g. a ballroom growing tense and shadowed as a "
    "stranger appears should set lighting_state even if nothing about the location's "
    "history changed), while profile_delta tracks narrative/backstory developments. "
    "Set whichever of the two actually changed -- do not default to only updating one of "
    "them. Within a state-change entry, only set the profile_delta field (and its "
    "sub-fields) if a personality/motivation/relationship/history actually changed this "
    "paragraph; leave it null otherwise -- never restate an unchanged value. Use only "
    "names that appear in the registry below; do not invent new entities. Respond with "
    "JSON matching the provided schema only.\n\n"
    "KNOWN CHARACTERS: {characters}\n"
    "KNOWN LOCATIONS: {locations}"
)


@dataclass
class SegmentedParagraph:
    sequence_index: int
    chapter_number: int
    raw_text: str


@dataclass
class RegistryMaps:
    """name/alias (lowercased) -> DB id, for both characters and locations."""

    character_id_by_name: dict[str, int] = field(default_factory=dict)
    location_id_by_name: dict[str, int] = field(default_factory=dict)
    character_summaries: list[str] = field(default_factory=list)
    location_summaries: list[str] = field(default_factory=list)
    character_profile_by_id: dict[int, dict] = field(default_factory=dict)
    location_profile_by_id: dict[int, dict] = field(default_factory=dict)

    def resolve_character(self, name: str) -> int | None:
        return self.character_id_by_name.get(name.strip().lower())

    def resolve_location(self, name: str) -> int | None:
        return self.location_id_by_name.get(name.strip().lower())


def segment_book_into_paragraphs(raw_book_text: str) -> list[SegmentedParagraph]:
    """Splits raw book text on blank lines into paragraphs, tracking chapter
    number via simple "Chapter N" heading detection.

    This is a deliberately simple, deterministic, non-LLM segmentation step:
    paragraph boundaries are a formatting fact about the source text, not
    something we want a 70B model's variance involved in.
    """
    chapter_number = 1
    segments: list[SegmentedParagraph] = []
    sequence_index = 0

    blocks = [block.strip() for block in raw_book_text.split("\n\n")]
    for block in blocks:
        if not block:
            continue
        if _CHAPTER_HEADING_RE.match(block):
            chapter_number += 1
            continue
        segments.append(
            SegmentedParagraph(sequence_index=sequence_index, chapter_number=chapter_number, raw_text=block)
        )
        sequence_index += 1

    return segments


def _chunked(items: list, size: int) -> list[list]:
    return [items[i : i + size] for i in range(0, len(items), size)]


def _normalize(name: str) -> str:
    return name.strip().lower()


def _merge_character_profile(base: dict, delta: CharacterProfileDelta | None) -> dict:
    """Overlays a sparse CharacterProfileDelta onto a full profile snapshot,
    producing the new full snapshot. Same carry-forward semantics as the
    scalar appearance_delta/emotional_state columns: a delta field left
    null/empty means 'no change', not 'erase'."""
    if delta is None:
        return base
    merged = dict(base)
    if delta.personality_shift:
        merged["personality_note"] = delta.personality_shift
    if delta.new_motivation:
        merged["motivations"] = delta.new_motivation
    if delta.relationship_changes:
        merged["relationships"] = {**base.get("relationships", {}), **delta.relationship_changes}
    return merged


def _merge_location_profile(base: dict, delta: LocationProfileDelta | None) -> dict:
    """Location equivalent of _merge_character_profile."""
    if delta is None:
        return base
    merged = dict(base)
    if delta.history_reveal:
        merged["history"] = delta.history_reveal
    if delta.narrative_significance_update:
        merged["narrative_significance"] = delta.narrative_significance_update
    return merged


# ---------------------------------------------------------------------------
# Pass 1: Global Registry
# ---------------------------------------------------------------------------


async def _extract_registry_chunk(
    pool: GpuWorkerPool, chunk_text: str
) -> RegistryExtractionResult:
    try:
        return await pool.extract_structured(
            system_prompt=REGISTRY_SYSTEM_PROMPT,
            user_prompt=chunk_text,
            response_schema=RegistryExtractionResult,
            max_tokens=8192,
        )
    except LLMExtractionError:
        logger.exception("Pass 1 registry extraction failed for a chunk; skipping it")
        return RegistryExtractionResult()


async def run_pass_1_registry(
    book_id: int, paragraphs: list[SegmentedParagraph], pool: GpuWorkerPool,
    on_chunk=None,
) -> RegistryMaps:
    """Extracts and persists Tier 1 (characters, locations) for the whole book."""

    # Large chunks (~40 paragraphs) keep the number of Pass-1 calls small;
    # registry extraction doesn't need paragraph-level granularity.
    paragraph_chunks = _chunked(paragraphs, size=40)
    chunk_texts = ["\n\n".join(p.raw_text for p in chunk) for chunk in paragraph_chunks]

    bar = tqdm(total=len(chunk_texts), desc="Pass 1 registry", unit="chunk", file=sys.stderr)

    async def _tracked_registry_chunk(text: str) -> RegistryExtractionResult:
        result = await _extract_registry_chunk(pool, text)
        bar.update(1)
        if on_chunk:
            on_chunk()
        return result

    results = await asyncio.gather(*(_tracked_registry_chunk(text) for text in chunk_texts))
    bar.close()

    merged_characters: dict[str, Character] = {}
    merged_locations: dict[str, Location] = {}

    for result in results:
        for candidate in result.characters:
            key = _normalize(candidate.canonical_name)
            if key not in merged_characters:
                merged_characters[key] = Character(
                    book_id=book_id,
                    canonical_name=candidate.canonical_name,
                    aliases=candidate.aliases,
                    baseline_visual_description=candidate.baseline_visual_description,
                    baseline_voice_description=candidate.baseline_voice_description,
                    extended_profile=candidate.profile.model_dump(),
                )
            else:
                # Merge in any newly-seen aliases for an already-known character.
                # First chunk's profile wins (same precedent as baseline_visual_description above).
                existing = merged_characters[key]
                existing.aliases = sorted(set(existing.aliases) | set(candidate.aliases))

        for candidate in result.locations:
            key = _normalize(candidate.canonical_name)
            if key not in merged_locations:
                merged_locations[key] = Location(
                    book_id=book_id,
                    canonical_name=candidate.canonical_name,
                    aliases=candidate.aliases,
                    baseline_visual_description=candidate.baseline_visual_description,
                    baseline_ambient_sfx_prompt=candidate.baseline_ambient_sfx_prompt,
                    extended_profile=candidate.profile.model_dump(),
                )
            else:
                existing = merged_locations[key]
                existing.aliases = sorted(set(existing.aliases) | set(candidate.aliases))

    maps = RegistryMaps()
    async with db_session_scope() as session:
        session.add_all(merged_characters.values())
        session.add_all(merged_locations.values())
        await session.flush()  # assigns character_id/location_id without ending the transaction

        for character in merged_characters.values():
            maps.character_id_by_name[_normalize(character.canonical_name)] = character.character_id
            for alias in character.aliases:
                maps.character_id_by_name[_normalize(alias)] = character.character_id
            maps.character_summaries.append(f"{character.canonical_name} (aliases: {', '.join(character.aliases) or 'none'})")
            maps.character_profile_by_id[character.character_id] = character.extended_profile

        for location in merged_locations.values():
            maps.location_id_by_name[_normalize(location.canonical_name)] = location.location_id
            for alias in location.aliases:
                maps.location_id_by_name[_normalize(alias)] = location.location_id
            maps.location_summaries.append(f"{location.canonical_name} (aliases: {', '.join(location.aliases) or 'none'})")
            maps.location_profile_by_id[location.location_id] = location.extended_profile

        book = await session.get(Book, book_id)
        book.ingestion_status = "registry_pass_complete"

    logger.info(
        "Pass 1 complete: %d characters, %d locations registered for book_id=%d",
        len(merged_characters),
        len(merged_locations),
        book_id,
    )
    return maps


# ---------------------------------------------------------------------------
# Pass 2: Temporal Ledger + Paragraph Beats
# ---------------------------------------------------------------------------


def _format_paragraph_batch_prompt(batch: list[SegmentedParagraph]) -> str:
    lines = [f"[sequence_index={p.sequence_index}] {p.raw_text}" for p in batch]
    return "\n\n".join(lines)


async def _extract_beats_chunk(
    pool: GpuWorkerPool, batch: list[SegmentedParagraph], system_prompt: str
) -> list[ParagraphBeat]:
    try:
        result: ParagraphBatchExtractionResult = await pool.extract_structured(
            system_prompt=system_prompt,
            user_prompt=_format_paragraph_batch_prompt(batch),
            response_schema=ParagraphBatchExtractionResult,
            max_tokens=8192,
        )
        return result.beats
    except LLMExtractionError:
        logger.exception(
            "Pass 2 beat extraction failed for sequence_index range [%d, %d]; skipping",
            batch[0].sequence_index,
            batch[-1].sequence_index,
        )
        return []


async def _generate_all_beats(
    paragraphs: list[SegmentedParagraph], registry: RegistryMaps, pool: GpuWorkerPool,
    on_batch=None,
) -> list[ParagraphBeat]:
    system_prompt = BEATS_SYSTEM_PROMPT_TEMPLATE.format(
        characters="; ".join(registry.character_summaries) or "none",
        locations="; ".join(registry.location_summaries) or "none",
    )
    batches = list(_chunked(paragraphs, size=settings.paragraph_chunk_size))
    bar = tqdm(total=len(batches), desc="Pass 2 beats  ", unit="batch", file=sys.stderr)

    async def _tracked_beats_chunk(batch: list[SegmentedParagraph]) -> list[ParagraphBeat]:
        result = await _extract_beats_chunk(pool, batch, system_prompt)
        bar.update(1)
        if on_batch:
            on_batch()
        return result

    # This is the data-parallel fan-out: every batch is an independent
    # coroutine, scheduled round-robin across all GPU replicas by GpuWorkerPool.
    results = await asyncio.gather(*(_tracked_beats_chunk(batch) for batch in batches))
    bar.close()

    all_beats = [beat for batch_result in results for beat in batch_result]
    all_beats.sort(key=lambda b: b.sequence_index)
    return all_beats


async def _apply_beats_sequentially(
    book_id: int,
    raw_text_by_sequence: dict[int, tuple[int, str]],  # sequence_index -> (chapter_number, raw_text)
    beats: list[ParagraphBeat],
    registry: RegistryMaps,
) -> None:
    """Writes paragraphs, paragraph_characters, and Tier 2 state deltas to
    Postgres in strict sequence_index order, maintaining an in-memory
    carry-forward cache so every Tier 2 row written holds a complete
    (not sparse) snapshot of the entity's current state.
    """
    character_latest_state: dict[int, CharacterState] = {}
    location_latest_state: dict[int, LocationState] = {}

    async with db_session_scope() as session:
        for beat in beats:
            if beat.sequence_index not in raw_text_by_sequence:
                logger.warning("Beat referenced unknown sequence_index=%d; skipping", beat.sequence_index)
                continue
            chapter_number, raw_text = raw_text_by_sequence[beat.sequence_index]

            active_location_id = (
                registry.resolve_location(beat.active_location_name) if beat.active_location_name else None
            )

            dialogue_payload = []
            for line in beat.dialogue_script:
                character_id = registry.resolve_character(line.character_name)
                if character_id is None:
                    logger.warning(
                        "Dialogue references unknown character '%s' at sequence_index=%d; dropping line",
                        line.character_name,
                        beat.sequence_index,
                    )
                    continue
                dialogue_payload.append(
                    {
                        "character_id": character_id,
                        "line": line.line,
                        "emotion": line.emotion,
                        "delivery": line.delivery,
                    }
                )

            paragraph = Paragraph(
                book_id=book_id,
                chapter_number=chapter_number,
                sequence_index=beat.sequence_index,
                raw_text=raw_text,
                active_location_id=active_location_id,
                camera_framing=beat.camera_framing,
                action_summary=beat.action_summary,
                dialogue_script=dialogue_payload,
                sfx_prompts=beat.sfx_prompts,
            )
            session.add(paragraph)
            await session.flush()  # assigns paragraph.paragraph_id for FK use below

            for character_name in beat.active_character_names:
                character_id = registry.resolve_character(character_name)
                if character_id is None:
                    logger.warning(
                        "Active character '%s' not found in registry at sequence_index=%d; skipping link",
                        character_name,
                        beat.sequence_index,
                    )
                    continue
                session.add(ParagraphCharacter(paragraph_id=paragraph.paragraph_id, character_id=character_id))

            # The LLM occasionally emits more than one state-change entry for
            # the same character within a single beat. Treat those as one
            # logical change (last non-null field wins) -- applying them as
            # separate ledger rows would make the second row close the
            # first one's range at the same paragraph_id it opened on,
            # violating chk_character_state_range (valid_from == valid_until).
            merged_character_changes: dict[str, CharacterStateChange] = {}
            for change in beat.character_state_changes:
                existing = merged_character_changes.get(change.character_name)
                if existing is None:
                    merged_character_changes[change.character_name] = change
                else:
                    merged_profile_delta = None
                    if existing.profile_delta or change.profile_delta:
                        existing_pd = existing.profile_delta
                        change_pd = change.profile_delta
                        merged_profile_delta = CharacterProfileDelta(
                            personality_shift=(change_pd and change_pd.personality_shift)
                            or (existing_pd and existing_pd.personality_shift),
                            new_motivation=(change_pd and change_pd.new_motivation)
                            or (existing_pd and existing_pd.new_motivation),
                            relationship_changes={
                                **((existing_pd and existing_pd.relationship_changes) or {}),
                                **((change_pd and change_pd.relationship_changes) or {}),
                            },
                        )
                    merged_character_changes[change.character_name] = CharacterStateChange(
                        character_name=change.character_name,
                        appearance_delta=change.appearance_delta or existing.appearance_delta,
                        emotional_state=change.emotional_state or existing.emotional_state,
                        vocal_delta_prompt=change.vocal_delta_prompt or existing.vocal_delta_prompt,
                        profile_delta=merged_profile_delta,
                    )

            for change in merged_character_changes.values():
                character_id = registry.resolve_character(change.character_name)
                if character_id is None:
                    logger.warning(
                        "State change for unknown character '%s' at sequence_index=%d; skipping",
                        change.character_name,
                        beat.sequence_index,
                    )
                    continue

                previous_state = character_latest_state.get(character_id)
                previous_profile = (
                    previous_state.profile_snapshot if previous_state is not None else None
                ) or registry.character_profile_by_id.get(character_id, {})
                new_state = CharacterState(
                    character_id=character_id,
                    valid_from_paragraph_id=paragraph.paragraph_id,
                    valid_until_paragraph_id=None,
                    appearance_delta=change.appearance_delta
                    or (previous_state.appearance_delta if previous_state else None),
                    emotional_state=change.emotional_state
                    or (previous_state.emotional_state if previous_state else None),
                    vocal_delta_prompt=change.vocal_delta_prompt
                    or (previous_state.vocal_delta_prompt if previous_state else None),
                    profile_snapshot=_merge_character_profile(previous_profile, change.profile_delta),
                )
                if previous_state is not None:
                    previous_state.valid_until_paragraph_id = paragraph.paragraph_id
                session.add(new_state)
                character_latest_state[character_id] = new_state

            # Same dedup as character_state_changes above, for the same reason.
            merged_location_changes: dict[str, LocationStateChange] = {}
            for change in beat.location_state_changes:
                existing = merged_location_changes.get(change.location_name)
                if existing is None:
                    merged_location_changes[change.location_name] = change
                else:
                    merged_profile_delta = None
                    if existing.profile_delta or change.profile_delta:
                        existing_pd = existing.profile_delta
                        change_pd = change.profile_delta
                        merged_profile_delta = LocationProfileDelta(
                            history_reveal=(change_pd and change_pd.history_reveal)
                            or (existing_pd and existing_pd.history_reveal),
                            narrative_significance_update=(change_pd and change_pd.narrative_significance_update)
                            or (existing_pd and existing_pd.narrative_significance_update),
                        )
                    merged_location_changes[change.location_name] = LocationStateChange(
                        location_name=change.location_name,
                        atmosphere_delta=change.atmosphere_delta or existing.atmosphere_delta,
                        lighting_state=change.lighting_state or existing.lighting_state,
                        ambient_sfx_delta=change.ambient_sfx_delta or existing.ambient_sfx_delta,
                        profile_delta=merged_profile_delta,
                    )

            for change in merged_location_changes.values():
                location_id = registry.resolve_location(change.location_name)
                if location_id is None:
                    logger.warning(
                        "State change for unknown location '%s' at sequence_index=%d; skipping",
                        change.location_name,
                        beat.sequence_index,
                    )
                    continue

                previous_state = location_latest_state.get(location_id)
                previous_profile = (
                    previous_state.profile_snapshot if previous_state is not None else None
                ) or registry.location_profile_by_id.get(location_id, {})
                new_state = LocationState(
                    location_id=location_id,
                    valid_from_paragraph_id=paragraph.paragraph_id,
                    valid_until_paragraph_id=None,
                    atmosphere_delta=change.atmosphere_delta
                    or (previous_state.atmosphere_delta if previous_state else None),
                    lighting_state=change.lighting_state
                    or (previous_state.lighting_state if previous_state else None),
                    ambient_sfx_delta=change.ambient_sfx_delta
                    or (previous_state.ambient_sfx_delta if previous_state else None),
                    profile_snapshot=_merge_location_profile(previous_profile, change.profile_delta),
                )
                if previous_state is not None:
                    previous_state.valid_until_paragraph_id = paragraph.paragraph_id
                session.add(new_state)
                location_latest_state[location_id] = new_state

            # Flush periodically (not strictly required) so FK lookups above
            # always see committed paragraph_ids without holding the whole
            # book's writes in one giant pending transaction.
            await session.flush()

        book = await session.get(Book, book_id)
        book.ingestion_status = "beats_pass_complete"

    logger.info("Pass 2 complete: %d paragraph beats written for book_id=%d", len(beats), book_id)


async def run_pass_2_beats(
    book_id: int, paragraphs: list[SegmentedParagraph], registry: RegistryMaps, pool: GpuWorkerPool,
    on_batch=None,
) -> None:
    beats = await _generate_all_beats(paragraphs, registry, pool, on_batch=on_batch)
    raw_text_by_sequence = {p.sequence_index: (p.chapter_number, p.raw_text) for p in paragraphs}
    await _apply_beats_sequentially(book_id, raw_text_by_sequence, beats, registry)


# ---------------------------------------------------------------------------
# Entry point
# ---------------------------------------------------------------------------


async def ingest_book(
    *, title: str, author: str | None, source_uri: str, raw_book_text: str, progress_cb=None
) -> int:
    """Runs the full two-pass ingestion pipeline for one book end-to-end.

    Returns the new book_id. Raises on unrecoverable errors (e.g. zero
    paragraphs found); per-chunk LLM failures are logged and skipped rather
    than aborting the whole run, so a single malformed chunk never loses an
    entire book's ingestion progress.

    `progress_cb(stage: str, completed: int, total: int)` is called as each
    LLM unit (registry chunk / beat batch) finishes, so a UI can show a
    progress bar + ETA. The completed/total are global across both passes.
    """
    import math

    paragraphs = segment_book_into_paragraphs(raw_book_text)
    if not paragraphs:
        raise ValueError(f"No paragraphs could be segmented from source_uri={source_uri!r}")

    # Total LLM "units": Pass-1 registry chunks (~40 paras each) + Pass-2 beat batches.
    registry_units = max(1, math.ceil(len(paragraphs) / 40))
    beat_units = max(1, math.ceil(len(paragraphs) / settings.paragraph_chunk_size))
    total_units = registry_units + beat_units
    completed = 0

    def _bump(stage: str) -> None:
        nonlocal completed
        completed += 1
        if progress_cb:
            progress_cb(stage, min(completed, total_units), total_units)

    if progress_cb:
        progress_cb("Preparing", 0, total_units)

    async with db_session_scope() as session:
        book = Book(title=title, author=author, source_uri=source_uri)
        session.add(book)
        await session.flush()
        book_id = book.book_id

    pool = GpuWorkerPool()
    try:
        registry = await run_pass_1_registry(
            book_id, paragraphs, pool, on_chunk=lambda: _bump("Analyzing characters & locations")
        )
        await run_pass_2_beats(
            book_id, paragraphs, registry, pool, on_batch=lambda: _bump("Extracting scene beats")
        )
        if progress_cb:
            progress_cb("Saving to library", total_units, total_units)
    except Exception:
        async with db_session_scope() as session:
            book = await session.get(Book, book_id)
            book.ingestion_status = "failed"
        logger.exception("Ingestion failed for book_id=%d", book_id)
        raise
    finally:
        await pool.aclose()

    return book_id


async def _main() -> None:
    import argparse
    import pathlib

    logging.basicConfig(level=logging.INFO)

    parser = argparse.ArgumentParser(description="Ingest a book into the world-state database.")
    parser.add_argument("book_path", type=pathlib.Path, help="Path to a plain-text book file")
    parser.add_argument("--title", required=True)
    parser.add_argument("--author", default=None)
    args = parser.parse_args()

    raw_text = args.book_path.read_text(encoding="utf-8")
    book_id = await ingest_book(
        title=args.title, author=args.author, source_uri=str(args.book_path), raw_book_text=raw_text
    )
    logger.info("Ingestion complete. book_id=%d", book_id)


if __name__ == "__main__":
    asyncio.run(_main())
