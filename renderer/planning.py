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

from renderer.config import settings
from renderer.schemas import (
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

SYSTEM_PROMPT = f"""You are a cinematographer breaking a book scene down into \
shots for a text-to-video diffusion model.

Given the resolved story state for a reader's highlighted passage, break it into \
the natural SEQUENCE of beats it contains -- one shot per distinct action or \
moment, in the order they happen. A passage where a character "leaves his house, \
gets in his car, and drives away" is THREE shots in sequence, NOT one blended \
shot -- the renderer plays them back to back so each action reads clearly \
instead of melting together. Use as many shots as the passage has real beats, \
up to {settings.max_video_shots_per_scene}. A passage that is a single vivid \
image or one unbroken action is just one shot. Do NOT pad: only emit a shot for \
something that actually happens in the highlighted lines, and keep the beats in \
story order.

CRITICAL, identity preservation: within a single continuous take the renderer \
MORPHS smoothly from one shot's picture into the next, so within ONE scene you \
must NEVER alternate between separate close-ups of two DIFFERENT characters -- \
the morph would literally turn one person's face into the other's. If two \
characters share a continuous moment, frame them TOGETHER in one sustained \
shot, each held in a distinct part of the frame, rather than cutting between \
individual close-ups. The ONE exception is a genuine scene break: when the next \
shot is a truly distinct scene, subject, or vantage, mark it 'cut_new_scene' -- \
the renderer then performs a REAL hard cut (not a morph), so you MAY switch to \
a different single subject there. This is exactly how to handle, e.g., a \
character's face and then a separate insert of the thing they are looking at: \
two shots, the second a 'cut_new_scene'. When in doubt within one continuous \
action, use one shot.

GROUND EVERY SHOT IN THE HIGHLIGHTED PASSAGE. Depict what THESE exact sentences \
describe -- the specific images, objects, gazes, faces, and reactions the reader \
selected -- not the wider chapter. If the passage dwells on a single vivid image \
(an eye, a hand, an object, a facial reaction), make THAT image the subject of \
the shot. The character/setting references and the "wider scene context" you are \
given are only there to tell you how people and places LOOK and what stays \
continuous; they must never replace what the highlighted lines literally say is \
happening. Read the passage and ask: what would a reader actually picture here?

For each shot, write these fields:
- camera: the shot type and camera angle/movement only, e.g. "Cinematic wide \
low-angle establishing shot, the camera slowly pushing in."
- action: the precise physical action in THIS shot only. Lead with what is \
happening, then -- for each visible character -- their emotional expression \
(face and body language) and their exact position and blocking in the frame \
and relative to each other and the setting. Show the feeling and the staging; \
do NOT redescribe what anyone or the place looks like (that is supplied \
separately). If the shot is a pure insert on a single object or body part (an \
eye, a hand, a letter), describe exactly ONE of it -- "a single clouded eye", \
never "eyes" -- and leave subjects empty so no other character is pulled into \
frame. e.g. "She freezes mid-step at the threshold, eyes wide with dread, one \
hand braced against the doorframe as he advances from the shadows behind her."
- subjects: the names of ONLY the characters actually VISIBLE on screen in \
THIS shot -- a subset of the scene's characters. These, and only these, get \
described and rendered in the shot; anyone you leave out does NOT appear in the \
frame. Use an empty list for a pure setting shot or an insert on an object or \
body part (an eye, a hand). Do NOT list a character merely because they matter \
to the moment or are referred to -- list them only if the camera literally sees \
them this shot. A single-character close-up has exactly one name here.
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
    action: str = Field(..., description="The action in this shot: motion + each visible character's emotional expression and exact position/blocking -- no appearance or setting description")
    subjects: list[str] = Field(default_factory=list, description="Names of ONLY the characters actually visible on screen this shot (subset of the given characters); these alone are described and rendered, anyone omitted does not appear. Empty for a pure setting shot or an insert on an object/body part. A single-character close-up lists exactly one name.")
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
    # The highlighted passage is THE subject -- everything else is reference so the
    # planner knows how things look and what stays continuous, not what to depict.
    lines: list[str] = [
        "HIGHLIGHTED PASSAGE -- this is exactly what to visualise:",
        scene.selected_text,
        "",
        "--- Reference only (how things look / continuity; do NOT treat as the thing to depict) ---",
    ]

    if scene.location is not None:
        location_line = f"Setting looks like -- {scene.location.name}: {scene.location.visual_description}"
        if scene.location.lighting_state:
            location_line += f" Lighting: {scene.location.lighting_state}."
        lines.append(location_line)

    for character in scene.characters:
        bit = f"{character.name} looks like -- {character.visual_description}"
        if character.emotional_state:
            bit += f" Current state: {character.emotional_state}."
        lines.append(bit)

    if scene.dialogue_script:
        speaking = ", ".join(sorted({line.character_name for line in scene.dialogue_script}))
        lines.append(f"Characters speaking during this span: {speaking}.")

    lines.append(f"Suggested camera framing: {scene.camera_framing.replace('_', ' ')}")
    lines.append(
        "Wider scene context (BACKGROUND ONLY -- this describes the surrounding chapter and "
        "usually extends beyond the highlighted lines; do not depict it unless the highlighted "
        f"passage itself does): {scene.action_summary}"
    )
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


def _split_on(text: str, marker: str) -> tuple[str, str | None]:
    """Split a resolved description into (baseline, delta) on the RPC's marker
    ('; currently: ' for a character's position, '; atmosphere: ' for a place's
    mood). The delta is the scene-specific signal we want to surface."""
    if marker in text:
        base, delta = text.split(marker, 1)
        return base.strip().rstrip(";").strip(), (delta.strip().rstrip(".") or None)
    return text.strip(), None


def _identity(baseline: str, max_chars: int = 180) -> str:
    """The core identity anchor: first sentence of the baseline appearance,
    capped. Enough to keep a character/place recognizable shot to shot without
    flooding the diffusion text encoder with the full profile every time."""
    core = baseline.split(". ")[0].strip()
    if len(core) > max_chars:
        core = core[:max_chars].rsplit(" ", 1)[0]
    return core.rstrip(" .,;:-")


def _build_prompt(*, camera: str, action: str, light: str, subjects: list[str], world: VideoWorldPayload) -> str:
    """Splice the Claude shot (camera/action/light) with COMPACT, signal-first
    world anchors, each character led by emotion + position so the model renders
    the feeling and the staging rather than a wall of repeated appearance text.

    ON-SCREEN ONLY: describe just the characters the planner marked as `subjects`
    (visible this shot). A single-subject insert (one face, one eye) must not
    drag the rest of the cast into frame -- that is what put two vulture eyes in
    a one-eye shot. Cross-shot identity morphing is handled upstream: within a
    continuous take the planner frames interacting characters TOGETHER in one
    shot, and a genuine scene change is a 'cut_new_scene' that the engine renders
    as a true hard cut rather than a morph."""
    parts: list[str] = [camera, action]

    # Describe ONLY who/what is on screen in THIS shot. A single-subject insert
    # (one face, one eye) must not drag the rest of the cast into frame -- that is
    # what put two vulture eyes in a one-eye shot. Cross-shot identity morphing is
    # prevented upstream: within a continuous take the planner frames interacting
    # characters TOGETHER in one shot; switching to a different lone subject only
    # happens at a 'cut_new_scene', which the engine renders as a true hard cut.
    names = [n for n in subjects if n in world.characters]
    if len(names) > 1:
        parts.append(
            f"{len(names)} distinct, separate people, each kept in a different part of "
            "the frame; they never blend into one face, merge, or swap identities:"
        )
    for name in names:
        baseline, position = _split_on(world.characters[name], "; currently: ")
        lead = [name]
        if (emotion := world.character_status.get(name)):
            lead.append(emotion)
        if position:
            lead.append(position)
        parts.append(f"{', '.join(lead)}: {_identity(baseline)}.")

    if world.location:
        base_loc, atmosphere = _split_on(world.location, "; atmosphere: ")
        setting = f"Setting: {_identity(base_loc, max_chars=200)}."
        if atmosphere:
            setting += f" {atmosphere}."
        parts.append(setting)

    parts.append(light)
    parts.append(world.look)
    return " ".join(part.strip() for part in parts if part and part.strip())


_STEER_SYSTEM = """You edit ONE continuous video shot, frame by frame, for a \
text-to-video model.

You are given the CURRENT on-screen description (what the frame shows right now) \
and a requested CHANGE. Rewrite the description so that ONLY the requested change \
is applied and EVERYTHING ELSE stays identical -- same character(s) and their \
identity/build/wardrobe, same setting, same lighting and mood, same framing. The \
result must read as a smooth edit of the SAME frame, not a new scene or a \
different person.

Output ONLY the new description: a single vivid present-tense paragraph of \
exactly what is now on screen. It must be a DESCRIPTION, never an instruction -- \
do NOT write "change", "now", "instead of", "transitions to", or name the old \
state; just describe the final picture. Keep the cinematic style wording intact. \
Be concise."""


def _fallback_steer(current: str, change: str) -> str:
    """No-LLM fallback: keep the current description and append the change as a
    descriptive clause. Weaker than the LLM edit but still a full prompt."""
    current, change = current.strip(), change.strip()
    if not change:
        return ""
    return f"{current} {change}".strip() if current else change


async def compose_steer_prompt(current_description: str, user_change: str, style: str = "") -> str:
    """Build the NEXT frame's description from the CURRENT frame's description and
    the user's requested change (this is the "each frame has a description; build
    the change off it" model). Using Claude to merge them yields a target that
    shares the subject/setting and differs only in the change -- so the engine's
    ramp_to morph is focused (hood -> cap on the SAME man) instead of reinventing
    the character. Falls back to appending the change if no API key / on error."""
    change = (user_change or "").strip()
    if not change:
        return ""
    if not settings.anthropic_api_key:
        return _fallback_steer(current_description, change)
    client = anthropic.AsyncAnthropic(api_key=settings.anthropic_api_key)
    try:
        # Fast/cheap model (Haiku): this merge is small, frequent, and latency-
        # sensitive -- no need for the heavier planning model here.
        resp = await client.messages.create(
            model=settings.claude_fast_model,
            max_tokens=500,
            system=_STEER_SYSTEM,
            messages=[{"role": "user",
                       "content": f"CURRENT:\n{current_description}\n\nCHANGE:\n{change}"}],
        )
        text = "".join(b.text for b in resp.content if getattr(b, "type", None) == "text").strip()
        if not text:
            return _fallback_steer(current_description, change)
        if style and style.strip() and style.strip() not in text:
            text = f"{text} {style.strip()}"
        return text
    except anthropic.APIError as exc:
        logger.warning("steer composition failed (%s) -- falling back to append", exc)
        return _fallback_steer(current_description, change)


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


def bootstrap_plan(scene: ComposedScenePayload) -> VideoPlanPayload:
    """A deterministic ONE-shot plan with NO Claude call, so the render can start
    the INSTANT /generate is hit (Phase 7 / M2). Built from the same anchors the
    real planner uses -- `_build_world` + `_build_prompt` over the scene's own
    camera framing, action summary, and full cast -- so the first frame is already
    grounded in character/setting/style. The refined Claude plan is morphed in
    later, on the same running rollout."""
    world = _build_world(scene)
    prompt = _build_prompt(
        camera=scene.camera_framing.replace("_", " "),
        action=scene.action_summary,
        light="",
        subjects=[c.name for c in scene.characters],
        world=world,
    )
    shot = VideoShotPayload(
        shot_id="00_bootstrap",
        camera=scene.camera_framing.replace("_", " "),
        action=scene.action_summary,
        light="",
        continuity="cut_new_scene",
        prompt=prompt,
        audio_prompt="",
    )
    return VideoPlanPayload(world=world, shots=[shot], negative_prompt=settings.video_negative_prompt)


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
                    subjects=shot.subjects, world=world,
                ),
                # AUDIO PAUSED: audio_prompt=_build_shot_audio_prompt(...)
                audio_prompt="",
            )
        )
    return VideoPlanPayload(world=world, shots=shots, negative_prompt=settings.video_negative_prompt)
