"""Shot planning, moved onto the VM renderer (off the old FastAPI middle tier).

Two steps, both operating purely on the resolved-context payloads the frontend
posts to /generate (no DB access):

1. compose_scene(): merge the per-paragraph contexts of a highlighted span into
   one self-contained scene (dedupe characters, resolve current location, merge
   dialogue/sfx).
2. generate_video_plan(): ask Claude (structured outputs) for a 1-4 shot
   cinematic breakdown, then deterministically splice the fixed world anchors +
   style + continuity into each shot's final text-to-video prompt.

AUDIO PAUSED: audio-prompt construction is commented out (search "AUDIO PAUSED")
so it's a one-step revive later. Dialogue is still merged + shown to the planner
(it informs framing), just not turned into audio prompts.
"""
from __future__ import annotations

import asyncio
import logging
from typing import Literal

import anthropic
from pydantic import BaseModel, Field, ValidationError

from services.renderer.config import settings
from services.renderer.schemas import (
    CharacterContextPayload,
    ComposedScenePayload,
    DialogueLinePayload,
    GenerationContextPayload,
    LocationContextPayload,
    VideoPlanPayload,
    VideoShotPayload,
    VideoWorldPayload,
)

logger = logging.getLogger("renderer.planning")

_MAX_RETRIES = 3


class VideoPlanningError(RuntimeError):
    """Raised when Claude could not produce a valid ShotBreakdown after retries,
    or when no API key is configured."""


# ===========================================================================
# Scene composition (was app/scene_composer.py)
# ===========================================================================


def _dedupe_characters(payloads: list[GenerationContextPayload]) -> list[CharacterContextPayload]:
    by_id: dict[int, CharacterContextPayload] = {}
    for payload in payloads:
        for character in payload.characters:
            by_id[character.character_id] = character
    return list(by_id.values())


def _resolve_location(
    payloads: list[GenerationContextPayload],
) -> tuple[LocationContextPayload | None, list[str]]:
    locations = [p.location for p in payloads if p.location is not None]
    if not locations:
        return None, []
    current = locations[-1]
    seen_names: list[str] = []
    for location in locations:
        if location.name not in seen_names:
            seen_names.append(location.name)
    transitions = [name for name in seen_names if name != current.name] if len(seen_names) > 1 else []
    return current, transitions


def _merge_dialogue(payloads: list[GenerationContextPayload]) -> list[DialogueLinePayload]:
    merged: list[DialogueLinePayload] = []
    for payload in payloads:
        merged.extend(payload.dialogue_script)
    return merged


def _merge_sfx(payloads: list[GenerationContextPayload]) -> list[str]:
    seen: list[str] = []
    for payload in payloads:
        for prompt in payload.sfx_prompts:
            if prompt not in seen:
                seen.append(prompt)
    return seen


def compose_scene(payloads: list[GenerationContextPayload]) -> ComposedScenePayload:
    """Consolidate an ordered (by sequence_index) list of per-paragraph contexts
    into one merged scene payload."""
    if not payloads:
        raise ValueError("compose_scene requires at least one paragraph context")

    payloads = sorted(payloads, key=lambda p: p.sequence_index)
    book_id = payloads[0].book_id

    characters = _dedupe_characters(payloads)
    location, location_transitions = _resolve_location(payloads)
    dialogue_script = _merge_dialogue(payloads)
    sfx_prompts = _merge_sfx(payloads)

    selected_text = "\n\n".join(p.raw_text for p in payloads)
    camera_framing = payloads[-1].camera_framing
    action_summary = " Then, ".join(p.action_summary for p in payloads)
    if location_transitions:
        action_summary += f" The scene moves through {', '.join(location_transitions)} before this point."

    # AUDIO PAUSED: audio_prompt = _build_audio_prompt(characters, dialogue_script, sfx_prompts, ...)
    audio_prompt = ""

    return ComposedScenePayload(
        book_id=book_id,
        paragraph_ids=[p.paragraph_id for p in payloads],
        sequence_index_range=(payloads[0].sequence_index, payloads[-1].sequence_index),
        selected_text=selected_text,
        characters=characters,
        location=location,
        dialogue_script=dialogue_script,
        sfx_prompts=sfx_prompts,
        camera_framing=camera_framing,
        action_summary=action_summary,
        video=None,
        audio_prompt=audio_prompt,
    )


# ===========================================================================
# Shot planning (was app/video_prompting.py)
# ===========================================================================

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

For each shot, write THREE separate fields:
- camera: the shot type and camera angle/movement only, e.g. "Cinematic wide \
low-angle establishing shot, the camera slowly pushing in."
- action: the precise physical action happening in THIS shot only -- who does \
what, where they are relative to each other and the frame. Do not redescribe \
what any character or the setting looks like; that is supplied separately and \
must not be repeated or contradicted here.
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
subject, but still the SAME scene as the previous shot -- no location change, \
no time jump. This is the right choice for most multi-shot breakdowns of one \
continuous moment.
  - "cut_new_scene": a hard break to a genuinely different scene -- a \
location change, a time jump, or an unrelated moment. Reserve this for \
real scene boundaries, not for ordinary cuts within one continuous action.
  The first shot in the sequence has no previous clip to relate to, so it \
is always "cut_new_scene".

Never mention paragraph numbers, character IDs, or any other database \
bookkeeping. Give each shot a short slug id like "01_the_chase" that \
reflects its place in sequence."""

ContinuityValue = Literal["continuous_frame", "cut_same_scene", "cut_new_scene"]


class ShotCandidate(BaseModel):
    shot_id: str = Field(..., description="Short slug reflecting sequence order, e.g. '01_the_chase'")
    camera: str = Field(..., description="Shot type and camera angle/movement only")
    action: str = Field(..., description="The physical action in this shot only -- no character or setting description")
    light: str = Field(..., description="Lighting and time of day for this shot only")
    continuity: ContinuityValue = Field(
        ...,
        description=(
            "'continuous_frame' if this clip opens on the previous clip's final frame "
            "(one unbroken take); 'cut_same_scene' for an ordinary cut to a new angle "
            "within the same ongoing scene; 'cut_new_scene' for an actual scene break "
            "(location/time change) -- always 'cut_new_scene' for the first shot"
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

    lines.append(f"Camera framing established by the text: {scene.camera_framing.replace('_', ' ')}")
    lines.append(f"Action across this span: {scene.action_summary}")
    return "\n".join(lines)


def _build_world(scene: ComposedScenePayload) -> VideoWorldPayload:
    """Assemble the fixed anchors from the resolved state -- appearance AND the
    current Tier-2 deltas (emotional status per character, lighting/atmosphere
    for the setting) -- so every shot is grounded in the full world-state."""
    return VideoWorldPayload(
        characters={c.name: c.visual_description for c in scene.characters},
        character_status={c.name: c.emotional_state for c in scene.characters if c.emotional_state},
        location=scene.location.visual_description if scene.location else None,
        atmosphere=(scene.location.lighting_state if scene.location else None),
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


def _build_prompt(*, camera: str, action: str, light: str, continuity: str, world: VideoWorldPayload) -> str:
    parts: list[str] = [camera, "Part of one continuous cinematic sequence.", action]
    for name, description in world.characters.items():
        line = f"This is the same individual {name} in every shot -- {description}."
        status = world.character_status.get(name)
        if status:
            line += f" Right now {name} is {status}."
        parts.append(line)
    if world.location:
        loc = f"The setting stays the same throughout -- {world.location}."
        if world.atmosphere:
            loc += f" The setting's current atmosphere: {world.atmosphere}."
        parts.append(loc)
    parts.append(light)
    parts.append(_CONTINUITY_NOTES[continuity])
    parts.append(world.look)
    return " ".join(part.strip() for part in parts if part.strip())


async def _request_shot_breakdown(user_prompt: str) -> ShotBreakdown:
    if not settings.anthropic_api_key:
        raise VideoPlanningError(
            "ANTHROPIC_API_KEY is not configured -- set it in ~/shotbook/.env and restart the renderer"
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
            logger.warning("Claude shot-planning attempt %d/%d failed: %s -- retrying in %.1fs",
                           attempt, _MAX_RETRIES, exc, backoff_s)
            if attempt < _MAX_RETRIES:
                await asyncio.sleep(backoff_s)

    raise VideoPlanningError(f"Exhausted {_MAX_RETRIES} attempts against the Claude API") from last_error


async def generate_video_plan(scene: ComposedScenePayload) -> VideoPlanPayload:
    """Plan camera/action/light per shot via Claude, then deterministically
    splice in the fixed world anchors + style + continuity so appearance/look
    never drift between shots."""
    result = await _request_shot_breakdown(_format_scene_for_llm(scene))
    world = _build_world(scene)

    shots: list[VideoShotPayload] = []
    for index, shot in enumerate(result.shots):
        # First shot has no previous clip -- enforce cut_new_scene deterministically.
        continuity = "cut_new_scene" if index == 0 else shot.continuity
        shots.append(
            VideoShotPayload(
                shot_id=shot.shot_id,
                camera=shot.camera,
                action=shot.action,
                light=shot.light,
                continuity=continuity,
                prompt=_build_prompt(
                    camera=shot.camera, action=shot.action, light=shot.light,
                    continuity=continuity, world=world,
                ),
                # AUDIO PAUSED: audio_prompt=_build_shot_audio_prompt(...)
                audio_prompt="",
            )
        )
    return VideoPlanPayload(world=world, shots=shots, negative_prompt=settings.video_negative_prompt)
