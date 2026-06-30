"""Pydantic response models for the on-demand context-compile API.

These shapes are the contract handed directly to the downstream generation
pipelines: `character.visual_description` / `location.visual_description`
go to the video diffusion model, `voice_description` / `voice_reference_audio_uri`
go to XTTS, and `sfx_prompts` go to the SFX (Stable Audio) model.
"""
from __future__ import annotations

from typing import Literal

from pydantic import BaseModel


class DialogueLinePayload(BaseModel):
    character_id: int
    character_name: str
    line: str
    emotion: str
    delivery: str


class CharacterContextPayload(BaseModel):
    character_id: int
    name: str
    visual_description: str
    voice_description: str
    voice_reference_audio_uri: str | None
    emotional_state: str | None
    profile: dict


class LocationContextPayload(BaseModel):
    location_id: int
    name: str
    visual_description: str
    lighting_state: str | None
    ambient_sfx_prompt: str
    profile: dict


class GenerationContextPayload(BaseModel):
    """The fully pre-compiled payload for one paragraph beat."""

    paragraph_id: int
    book_id: int
    sequence_index: int
    chapter_number: int
    raw_text: str
    camera_framing: str
    action_summary: str
    characters: list[CharacterContextPayload]
    location: LocationContextPayload | None
    dialogue_script: list[DialogueLinePayload]
    sfx_prompts: list[str]
    narrative_context: str


# ---------------------------------------------------------------------------
# Library / reader endpoints (app/routers/books.py)
# ---------------------------------------------------------------------------
class BookSummaryPayload(BaseModel):
    book_id: int
    title: str
    author: str | None
    ingestion_status: str
    paragraph_count: int


class ParagraphPayload(BaseModel):
    """A bare paragraph for the reader view -- text + identity only, no
    resolved state. State is fetched on demand once the reader selects a
    span of text and presses "query"."""

    paragraph_id: int
    sequence_index: int
    chapter_number: int
    raw_text: str


class BatchContextRequest(BaseModel):
    paragraph_ids: list[int]


class ComposeSceneRequest(BaseModel):
    paragraph_ids: list[int]


class VideoWorldPayload(BaseModel):
    """Fixed, scene-wide identity anchors -- the same role a hand-authored
    `WORLD = {"hare": ..., "world": ..., "look": ...}` dict plays: every
    shot's prompt splices these in verbatim, so appearance/setting/style
    never drift between shots."""

    characters: dict[str, str]
    location: str | None
    look: str


class VideoShotPayload(BaseModel):
    """One shot in the breakdown -- the camera/action/light the LLM planned
    for it, plus `prompt`, the fully assembled text-to-video prompt (world
    anchors + this shot's camera/action/light/continuity + style, in that
    order).

    `continuity` tells the video pipeline how this clip relates to the
    previous one: "continuous_frame" (opens on the previous clip's final
    frame -- one unbroken take, e.g. a held shot or camera move),
    "cut_same_scene" (an ordinary edit cut to a new angle/subject within the
    same ongoing scene, e.g. shot/reverse-shot dialogue -- no location or
    time change), or "cut_new_scene" (an actual scene break -- location
    change, time jump). Always "cut_new_scene" for the first shot in a plan."""

    shot_id: str
    camera: str
    action: str
    light: str
    continuity: Literal["continuous_frame", "cut_same_scene", "cut_new_scene"]
    prompt: str
    # The dialogue (+ ambient bed) voiced over THIS shot specifically, so it's
    # clear what is spoken per clip rather than one lump prompt for the span.
    audio_prompt: str = ""


class VideoPlanPayload(BaseModel):
    """Mirrors a hand-authored `WORLD = {...}; SCENES = [...]; NEG = "..."`
    script: fixed world anchors, an ordered shot list, and one shared
    negative prompt applied to every shot."""

    world: VideoWorldPayload
    shots: list[VideoShotPayload]
    negative_prompt: str


class ComposedScenePayload(BaseModel):
    """Output of the scene-consolidation step: every paragraph the reader's
    selection touched, merged into one self-contained scene description,
    plus an LLM-planned video shot breakdown and a flattened text prompt for
    the audio (XTTS dialogue / Stable Audio SFX) model."""

    book_id: int
    paragraph_ids: list[int]
    sequence_index_range: tuple[int, int]
    selected_text: str
    characters: list[CharacterContextPayload]
    location: LocationContextPayload | None
    dialogue_script: list[DialogueLinePayload]
    sfx_prompts: list[str]
    camera_framing: str
    action_summary: str
    video: VideoPlanPayload | None
    audio_prompt: str
