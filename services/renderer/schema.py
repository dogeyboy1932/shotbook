"""Request/response models for the renderer service.

The `shots` list mirrors `app/schemas.py::VideoShotPayload` (the output of
`app/video_prompting.py`). Only `prompt` is required here -- the camera/action/
light/world fields are already baked into `prompt` by the shot planner, so the
renderer just needs the final assembled text-to-video prompt per shot. Extra
fields on each shot are ignored.
"""
from __future__ import annotations

from pydantic import BaseModel, ConfigDict


class ShotIn(BaseModel):
    # Ignore the extra shot fields (camera/action/light) the planner includes --
    # `prompt` already contains the fully assembled text-to-video string.
    model_config = ConfigDict(extra="ignore")

    prompt: str
    shot_id: str | None = None
    continuity: str | None = None
    audio_prompt: str | None = None


class RenderRequest(BaseModel):
    shots: list[ShotIn]
    out_dir: str                      # absolute dir the final mp4 + per-shot clips land in
    seconds_per_shot: float = 5.0
    # Accepted for forward-compat; the distilled streaming model does not currently
    # consume a CFG negative prompt (the look/negatives are baked into the prompt).
    negative_prompt: str | None = None


class RenderResponse(BaseModel):
    video_path: str                   # absolute path to final_story.mp4
    shot_count: int
    total_frames: int
    seconds: float                    # wall-clock render time
