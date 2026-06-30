"""LLM-driven shot breakdown: turns a composed scene (the reader's
highlighted span, already resolved to character/location state by
app/scene_composer.py) into a video plan -- a fixed "world" of identity
anchors plus one or more cinematic text-to-video diffusion shots, mirroring
a hand-authored script of the shape:

    WORLD = {"hare": "...", "tortoise": "...", "world": "...", "look": "..."}
    SCENES = [("01_the_setup", build_prompt(camera=..., action=..., light=...)), ...]
    NEG = "..."

A short highlighted moment becomes a single shot. A longer span covering a
location change, a time jump, or a sequence of distinct physical actions
becomes several, meant to be generated as separate clips and stitched
together in order.

`world` and `negative_prompt` are built deterministically from the DB's
Tier 1/2 state -- the full character/location visual descriptions are fixed
identity anchors, spliced verbatim into every shot's `prompt`. The LLM is
asked for ONLY the camera, action, and lighting per shot, never the
character/setting prose -- letting it re-describe characters per shot is
exactly what caused identity drift (a full bio in shot 1, a fragment in
shot 2, nothing in shot 3) in earlier iterations of this module. Fixing the
anchors and varying only what actually changes per shot is what keeps a
multi-shot sequence visually continuous.

Calls the Claude API directly (not the local vLLM fleet that book ingestion
uses) via structured outputs -- `client.messages.parse()` constrains the
response to `ShotBreakdown` and hands back a validated instance, so the only
failure modes this module has to handle are transient API errors and the
rare schema-validation mismatch, never malformed JSON.
"""
from __future__ import annotations

import asyncio
import logging
from typing import Literal

import anthropic
from pydantic import BaseModel, Field, ValidationError

from app.config import settings
from app.schemas import (
    CharacterContextPayload,
    ComposedScenePayload,
    DialogueLinePayload,
    VideoPlanPayload,
    VideoShotPayload,
    VideoWorldPayload,
)

logger = logging.getLogger("app.video_prompting")

_MAX_RETRIES = 3


class VideoPlanningError(RuntimeError):
    """Raised when the Claude API call could not produce a valid ShotBreakdown
    after all retries are exhausted, or when no API key is configured."""

SYSTEM_PROMPT = """You are a cinematographer breaking a book scene down into \
shots for a text-to-video diffusion model.

Given the resolved story state for a reader's highlighted passage, decide \
whether it needs ONE shot or SEVERAL sequential shots that form one \
continuous cinematic scene. Use multiple shots only when the passage \
genuinely covers distinct beats -- a location change, a time jump, or a \
sequence of discrete physical actions. A single static moment should stay \
one shot. Never plan more shots than there are distinct beats. The \
pipeline renders about 5 seconds per shot and can handle a longer \
continuous scene plan when the passage truly needs it. Prefer a compact \
sequence of 1-4 shots, using seamless continuity transitions whenever the \
action genuinely flows across the beat.

For each shot, write FOUR separate fields:
- camera: the shot type and camera angle/movement only, e.g. "Cinematic wide \
low-angle establishing shot, the camera slowly pushing in."
- action: the precise physical action or dialogue beat happening in THIS \
shot only -- who does what, where they are relative to each other and the \
frame. Do not redescribe what any character or the setting looks like; that \
is supplied separately and must not be repeated or contradicted here.
- light: the lighting and time of day for this shot only, e.g. "Soft dawn \
golden-hour light."
- continuity: how this shot's video clip relates to the PREVIOUS shot's clip. \
One of three values:
  - "continuous_frame": this clip opens on the exact same frame the previous \
clip ended on -- the video model chains them into one unbroken take. Use \
this only when the camera and subject genuinely flow on without \
interruption: a held shot that simply continues, or a deliberate camera \
move/pan from the previous framing into this one.
  - "cut_same_scene": an ordinary edited cut to a different camera angle or \
subject (e.g. cutting between two people talking, or to a close-up), but \
still the SAME scene as the previous shot -- no location change, no time \
jump. This is the right choice for shot/reverse-shot dialogue and most \
multi-shot breakdowns of one continuous moment.
  - "cut_new_scene": a hard break to a genuinely different scene -- a \
location change, a time jump, or an unrelated moment. Reserve this for \
real scene boundaries, not for ordinary cuts within one conversation or \
one continuous action.
  The first shot in the sequence has no previous clip to relate to, so it \
is always "cut_new_scene".
- dialogue_line_indices: the indices of the numbered dialogue lines (listed \
in the scene description) that are SPOKEN during THIS shot. Assign each \
dialogue line to exactly one shot, in reading order, placing it in the shot \
whose action it accompanies. Use an empty list for a shot with no spoken \
dialogue. Do not invent lines or indices that are not in the list.

Never mention paragraph numbers, character IDs, or any other database \
bookkeeping. Give each shot a short slug id like "01_the_chase" that \
reflects its place in sequence."""

ContinuityValue = Literal["continuous_frame", "cut_same_scene", "cut_new_scene"]


class ShotCandidate(BaseModel):
    shot_id: str = Field(..., description="Short slug reflecting sequence order, e.g. '01_the_chase'")
    camera: str = Field(..., description="Shot type and camera angle/movement only")
    action: str = Field(..., description="The physical action/dialogue beat in this shot only -- no character or setting description")
    light: str = Field(..., description="Lighting and time of day for this shot only")
    continuity: ContinuityValue = Field(
        ...,
        description=(
            "'continuous_frame' if this clip opens on the previous clip's final frame "
            "(one unbroken take); 'cut_same_scene' for an ordinary cut to a new angle "
            "within the same ongoing scene (e.g. shot/reverse-shot dialogue); "
            "'cut_new_scene' for an actual scene break (location/time change) -- "
            "always 'cut_new_scene' for the first shot, since there is no previous clip"
        ),
    )
    dialogue_line_indices: list[int] = Field(
        default_factory=list,
        description=(
            "0-based indices into the numbered dialogue list from the scene that are "
            "spoken during this shot. Each dialogue line belongs to exactly one shot; "
            "empty list if no dialogue is spoken in this shot."
        ),
    )


class ShotBreakdown(BaseModel):
    shots: list[ShotCandidate] = Field(..., min_length=1, max_length=settings.max_video_shots_per_scene)


def _format_scene_for_llm(scene: ComposedScenePayload) -> str:
    lines: list[str] = [f"Passage text:\n{scene.selected_text}", ""]

    if scene.location is not None:
        location_line = f"Location: {scene.location.name} -- {scene.location.visual_description}"
        if scene.location.lighting_state:
            location_line += f" Lighting: {scene.location.lighting_state}."
        lines.append(location_line)

    for character in scene.characters:
        bit = f"Character {character.name}: {character.visual_description}"
        if character.emotional_state:
            bit += f" Currently: {character.emotional_state}."
        lines.append(bit)

    if scene.dialogue_script:
        speaking = ", ".join(sorted({line.character_name for line in scene.dialogue_script}))
        lines.append(f"Characters speaking on screen during this span: {speaking}.")
        lines.append(
            "Numbered dialogue lines (assign each to the shot it is spoken in, "
            "via that shot's dialogue_line_indices):"
        )
        for i, line in enumerate(scene.dialogue_script):
            lines.append(f"  [{i}] {line.character_name}: \"{line.line}\"")

    lines.append(f"Camera framing established by the text: {scene.camera_framing.replace('_', ' ')}")
    lines.append(f"Action across this span: {scene.action_summary}")
    return "\n".join(lines)


def _build_world(scene: ComposedScenePayload) -> VideoWorldPayload:
    """The fixed per-scene identity anchors -- same role as a hand-authored
    WORLD dict. Built once, deterministically, from the already-resolved
    Tier 1/2 state; never touched by the LLM."""
    return VideoWorldPayload(
        characters={c.name: c.visual_description for c in scene.characters},
        location=scene.location.visual_description if scene.location else None,
        look=settings.video_style_suffix,
    )


_CONTINUITY_NOTES = {
    "continuous_frame": (
        "This clip opens on the exact same frame the previous clip ended on -- "
        "continue that take without a cut."
    ),
    "cut_same_scene": (
        "This is an edited cut to a new angle, but still the same continuous scene as "
        "the previous clip -- keep the same setting, time of day, and atmosphere."
    ),
    "cut_new_scene": (
        "This is a hard cut to a new scene -- do not carry over the previous shot's "
        "framing, setting, or lighting."
    ),
}


def _build_prompt(
    *, camera: str, action: str, light: str, continuity: str, world: VideoWorldPayload
) -> str:
    """Assembles one shot's final prompt from the fixed world anchors plus
    this shot's camera/action/light/continuity, in a stable field order
    (identity early, style last) so every shot in a scene reads as the same
    continuous take rather than an independently regenerated description."""
    parts: list[str] = [camera, "Part of one continuous cinematic sequence.", action]
    parts.extend(
        f"This is the same individual {name} in every shot -- {description}."
        for name, description in world.characters.items()
    )
    if world.location:
        parts.append(f"The setting stays the same throughout -- {world.location}.")
    parts.append(light)
    parts.append(_CONTINUITY_NOTES[continuity])
    parts.append(world.look)
    return " ".join(part.strip() for part in parts if part.strip())


async def _request_shot_breakdown(user_prompt: str) -> ShotBreakdown:
    if not settings.anthropic_api_key:
        raise VideoPlanningError(
            "ANTHROPIC_API_KEY is not configured -- export it (or set it in .env) and restart the API"
        )

    client = anthropic.AsyncAnthropic(api_key=settings.anthropic_api_key)
    last_error: Exception | None = None
    for attempt in range(1, _MAX_RETRIES + 1):
        try:
            response = await client.messages.parse(
                model=settings.claude_video_model,
                max_tokens=1024,
                system=SYSTEM_PROMPT,
                messages=[{"role": "user", "content": user_prompt}],
                output_format=ShotBreakdown,
            )
            return response.parsed_output
        except (anthropic.APIError, ValidationError) as exc:
            last_error = exc
            backoff_s = min(2**attempt, 10)
            logger.warning(
                "Claude shot-planning attempt %d/%d failed: %s -- retrying in %.1fs",
                attempt,
                _MAX_RETRIES,
                exc,
                backoff_s,
            )
            if attempt < _MAX_RETRIES:
                await asyncio.sleep(backoff_s)

    raise VideoPlanningError(f"Exhausted {_MAX_RETRIES} attempts against the Claude API") from last_error


def _build_shot_audio_prompt(
    characters: list[CharacterContextPayload],
    dialogue_lines: list[DialogueLinePayload],
    ambient_sfx_prompt: str | None,
) -> str:
    """Per-shot audio prompt: the ambient bed (shared by every clip in the
    scene) plus only the dialogue spoken in THIS shot -- so it's clear what
    is voiced over each clip, rather than one lump prompt for the whole span.
    Mirrors the formatting of scene_composer._build_audio_prompt."""
    parts: list[str] = []
    if ambient_sfx_prompt:
        parts.append(f"Ambient bed: {ambient_sfx_prompt}.")
    if dialogue_lines:
        voice_by_name = {c.name: c.voice_description for c in characters}
        dialogue_bits = []
        for line in dialogue_lines:
            voice = voice_by_name.get(line.character_name, "")
            voice_note = f" [voice: {voice}]" if voice else ""
            dialogue_bits.append(
                f'{line.character_name} ({line.emotion}, {line.delivery}){voice_note}: "{line.line}"'
            )
        parts.append("Dialogue: " + " | ".join(dialogue_bits))
    return " ".join(parts) if parts else "No spoken dialogue in this shot."


def _assign_dialogue_to_shots(
    shots: list[ShotCandidate], dialogue_count: int
) -> list[list[int]]:
    """Resolve each shot's dialogue_line_indices to valid, de-duplicated index
    lists. Any line the model failed to assign to a shot is appended to the
    last shot so no dialogue is silently dropped; out-of-range/duplicate
    indices are discarded."""
    assigned: set[int] = set()
    per_shot: list[list[int]] = []
    for shot in shots:
        valid: list[int] = []
        for i in shot.dialogue_line_indices:
            if 0 <= i < dialogue_count and i not in assigned:
                assigned.add(i)        # mark immediately so within-list dupes drop too
                valid.append(i)
        per_shot.append(valid)

    leftover = [i for i in range(dialogue_count) if i not in assigned]
    if leftover and per_shot:
        per_shot[-1].extend(leftover)
    return per_shot


async def generate_video_plan(scene: ComposedScenePayload) -> VideoPlanPayload:
    """Plans the camera/action/light for each shot via the Claude API, then
    deterministically builds `world`, splices it (plus the shared style and
    negative prompt) into every shot's `prompt` -- so appearance and look
    never drift between shots or between scenes. Each shot also gets its own
    `audio_prompt` carrying only the dialogue spoken in that shot."""
    result = await _request_shot_breakdown(_format_scene_for_llm(scene))

    world = _build_world(scene)
    ambient = scene.location.ambient_sfx_prompt if scene.location else None
    dialogue_by_shot = _assign_dialogue_to_shots(result.shots, len(scene.dialogue_script))

    shots = []
    for index, shot in enumerate(result.shots):
        # The first shot has no previous clip to relate to, no matter what the
        # model said -- enforce that deterministically rather than trusting the
        # schema's free-text-adjacent enum to always get it right.
        continuity = "cut_new_scene" if index == 0 else shot.continuity
        shot_lines = [scene.dialogue_script[i] for i in dialogue_by_shot[index]]
        shots.append(
            VideoShotPayload(
                shot_id=shot.shot_id,
                camera=shot.camera,
                action=shot.action,
                light=shot.light,
                continuity=continuity,
                prompt=_build_prompt(
                    camera=shot.camera,
                    action=shot.action,
                    light=shot.light,
                    continuity=continuity,
                    world=world,
                ),
                audio_prompt=_build_shot_audio_prompt(scene.characters, shot_lines, ambient),
            )
        )
    return VideoPlanPayload(world=world, shots=shots, negative_prompt=settings.video_negative_prompt)
