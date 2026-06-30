"""Payload models for the renderer's planning layer.

Moved out of app/schemas.py so the VM renderer is self-contained (no FastAPI
middle tier, no SQLAlchemy). These are the shapes the React app sends to
POST /generate (the resolved context payloads from Supabase's resolve_contexts
RPC) and the shot-plan shapes the planner produces.

AUDIO PAUSED: audio_prompt fields are kept for a one-step revive but are not
populated right now (see renderer/planning.py).
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
    voice_reference_audio_uri: str | None = None
    emotional_state: str | None = None
    profile: dict = {}


class LocationContextPayload(BaseModel):
    location_id: int
    name: str
    visual_description: str
    lighting_state: str | None = None
    ambient_sfx_prompt: str
    profile: dict = {}


class GenerationContextPayload(BaseModel):
    """One resolved paragraph beat, as returned by the Supabase resolve_contexts
    RPC and posted to /generate. `narrative_context` is no longer produced by
    the RPC (the UI renders structured fields), so it's optional here."""

    paragraph_id: int
    book_id: int
    sequence_index: int
    chapter_number: int
    raw_text: str
    camera_framing: str
    action_summary: str
    characters: list[CharacterContextPayload] = []
    location: LocationContextPayload | None = None
    dialogue_script: list[DialogueLinePayload] = []
    sfx_prompts: list[str] = []
    narrative_context: str = ""


class VideoWorldPayload(BaseModel):
    """Scene-wide anchors spliced verbatim into every shot's prompt so the full
    resolved world-state -- identity AND current status -- is grounded in each
    clip, and never drifts between shots.

    - characters: name -> appearance (baseline + the active appearance delta)
    - character_status: name -> current emotional/physical status (Tier-2 delta)
    - location: setting appearance (baseline + active atmosphere delta)
    - atmosphere: the setting's current lighting/mood (Tier-2 location delta)
    """

    characters: dict[str, str]
    character_status: dict[str, str] = {}
    location: str | None
    atmosphere: str | None = None
    look: str


class VideoShotPayload(BaseModel):
    shot_id: str
    camera: str
    action: str
    light: str
    continuity: Literal["continuous_frame", "cut_same_scene", "cut_new_scene"]
    prompt: str
    # AUDIO PAUSED: per-shot dialogue/ambient bed; not populated right now.
    audio_prompt: str = ""


class VideoPlanPayload(BaseModel):
    world: VideoWorldPayload
    shots: list[VideoShotPayload]
    negative_prompt: str


class ComposedScenePayload(BaseModel):
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
    video: VideoPlanPayload | None = None
    # AUDIO PAUSED: flattened dialogue/SFX prompt; empty right now.
    audio_prompt: str = ""


class GenerateRequest(BaseModel):
    """Body for POST /generate: the resolved contexts for the highlighted span
    (straight from the Supabase RPC) plus an optional per-shot duration."""

    contexts: list[GenerationContextPayload]
    seconds_per_shot: float | None = None
