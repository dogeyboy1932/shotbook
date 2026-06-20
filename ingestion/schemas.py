"""Pydantic models describing the exact JSON shapes we force the LLM to emit
during each ingestion pass, via vLLM's guided/structured decoding
(`guided_json`, backed by outlines/lm-format-enforcer on the server side).

Because the schema is enforced server-side at the token level, the client
only needs to defend against truncated output (max_tokens cutoff) and
semantically-empty output (e.g. an empty `characters` list) -- not against
malformed JSON syntax, which guided decoding makes structurally impossible.
"""
from __future__ import annotations

from typing import Literal

from pydantic import BaseModel, Field

# ---------------------------------------------------------------------------
# Pass 1: Global Registry extraction
# ---------------------------------------------------------------------------


class CharacterProfile(BaseModel):
    """Baseline (immutable) narrative profile, stored as-is into
    characters.extended_profile JSONB. Kept as an explicit schema (not a
    free-form dict) so vLLM's structured decoding can actually constrain it
    -- a JSONB storage column does not mean the extraction schema should be
    unconstrained."""

    backstory: str = Field(..., description="The character's history prior to/independent of this book's events")
    personality_traits: list[str] = Field(default_factory=list, description="e.g. ['proud', 'impulsive', 'fiercely loyal']")
    speech_patterns: str = Field(..., description="How they talk: vocabulary, verbal tics, formality")
    motivations: str = Field(..., description="What this character currently wants/is driving toward")
    relationships: dict[str, str] = Field(
        default_factory=dict,
        description="Other characters' canonical_name -> this character's baseline relationship/feelings toward them",
    )


class LocationProfile(BaseModel):
    """Baseline (immutable) narrative profile, stored as-is into
    locations.extended_profile JSONB."""

    history: str = Field(..., description="This location's backstory/history prior to the book's events")
    narrative_significance: str = Field(..., description="Why this location matters to the story")


class CharacterCandidate(BaseModel):
    canonical_name: str = Field(..., description="The character's most formal/complete name")
    aliases: list[str] = Field(default_factory=list, description="Nicknames, titles, or shortened names used in the text")
    baseline_visual_description: str = Field(
        ..., description="Immutable physical description: face, build, base wardrobe, distinguishing features"
    )
    baseline_voice_description: str = Field(
        ..., description="Vocal character: timbre, accent, pace, register"
    )
    profile: CharacterProfile


class LocationCandidate(BaseModel):
    canonical_name: str
    aliases: list[str] = Field(default_factory=list)
    baseline_visual_description: str = Field(
        ..., description="Immutable establishing-shot description: architecture, terrain, color palette"
    )
    baseline_ambient_sfx_prompt: str = Field(
        ..., description="Default ambient soundscape text prompt for this location"
    )
    profile: LocationProfile


class RegistryExtractionResult(BaseModel):
    """Pass-1 LLM output for a single book chunk."""

    characters: list[CharacterCandidate] = Field(default_factory=list)
    locations: list[LocationCandidate] = Field(default_factory=list)


# ---------------------------------------------------------------------------
# Pass 2: Paragraph Beats + Temporal Ledger deltas
# ---------------------------------------------------------------------------

VALID_CAMERA_FRAMINGS = (
    "extreme_close_up",
    "close_up",
    "medium_shot",
    "wide_shot",
    "establishing_shot",
    "over_the_shoulder",
    "pov",
)


class DialogueLine(BaseModel):
    character_name: str = Field(..., description="Must match a canonical_name or alias from the registry")
    line: str
    emotion: str = Field(..., description="e.g. 'fearful', 'triumphant', 'flat affect'")
    delivery: str = Field(..., description="e.g. 'whispered', 'shouted across the room'")


class CharacterProfileDelta(BaseModel):
    """Sparse: only set fields that actually changed this paragraph. Merged
    into the character's running profile snapshot (see ingestion/orchestrator.py
    _merge_profile) -- unset fields here mean 'no change', not 'erase'."""

    personality_shift: str | None = Field(None, description="A new personality development, or null if unchanged")
    new_motivation: str | None = Field(None, description="An updated driving motivation, or null if unchanged")
    relationship_changes: dict[str, str] = Field(
        default_factory=dict,
        description="Other characters' canonical_name -> updated relationship, only for relationships that changed this paragraph",
    )


class LocationProfileDelta(BaseModel):
    """Sparse: only set fields that actually changed this paragraph."""

    history_reveal: str | None = Field(None, description="New historical detail revealed this paragraph, or null")
    narrative_significance_update: str | None = Field(None, description="Updated narrative significance, or null if unchanged")


class CharacterStateChange(BaseModel):
    """Emitted ONLY when this paragraph changes a character's appearance,
    emotional state, voice, personality, motivation, or a relationship
    relative to their last known state."""

    character_name: str
    appearance_delta: str | None = Field(None, description="New visual detail, or null if unchanged")
    emotional_state: str | None = None
    vocal_delta_prompt: str | None = Field(None, description="New vocal quality, or null if unchanged")
    profile_delta: CharacterProfileDelta | None = Field(
        None, description="Only present if personality/motivation/a relationship changed this paragraph"
    )


class LocationStateChange(BaseModel):
    """Emitted ONLY when this paragraph changes the active location's
    atmosphere, lighting, ambient sound, history, or narrative significance
    relative to its last known state."""

    location_name: str
    atmosphere_delta: str | None = None
    lighting_state: str | None = None
    ambient_sfx_delta: str | None = None
    profile_delta: LocationProfileDelta | None = Field(
        None, description="Only present if history/narrative significance changed this paragraph"
    )


class ParagraphBeat(BaseModel):
    """Pass-2 LLM output for a single paragraph."""

    sequence_index: int = Field(..., description="Echo back the input sequence_index unchanged")
    active_character_names: list[str] = Field(default_factory=list)
    active_location_name: str | None = None
    camera_framing: Literal[VALID_CAMERA_FRAMINGS]  # type: ignore[valid-type]
    action_summary: str = Field(..., description="One-line physical action/beat description for the video prompt")
    dialogue_script: list[DialogueLine] = Field(default_factory=list)
    sfx_prompts: list[str] = Field(default_factory=list, description="Discrete, isolated SFX text prompts")
    character_state_changes: list[CharacterStateChange] = Field(default_factory=list)
    location_state_changes: list[LocationStateChange] = Field(default_factory=list)


class ParagraphBatchExtractionResult(BaseModel):
    """Pass-2 LLM output for a chunk of N consecutive paragraphs."""

    beats: list[ParagraphBeat]
