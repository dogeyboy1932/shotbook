"""Renderer-service settings (planning + render).

Self-contained so the VM renderer no longer depends on app/config.py. Reads the
same ~/shotbook/.env the rest of the deploy uses (BVG_ prefix; ANTHROPIC_API_KEY
via its conventional name).
"""
from __future__ import annotations

from pydantic import Field
from pydantic_settings import BaseSettings, SettingsConfigDict


class RendererSettings(BaseSettings):
    model_config = SettingsConfigDict(env_file=".env", env_prefix="BVG_", extra="ignore")

    # --- Streaming diffusion model (loaded once at renderer startup) ----------
    # Quality vs speed knob -- the model variant the StreamingCF engine loads:
    #   "chunkwise" -- 4-step, 3 frames/block, higher quality (default)
    #   "fw2step"   -- 2-step, 1 frame/block, faster, lower quality
    # To trial a larger/custom checkpoint, add it to the MODELS dict in
    # renderer/vendor/cf_streaming.py and set BVG_RENDERER_MODEL to its key.
    renderer_model: str = "chunkwise"

    # --- Claude shot planning (moved off the old FastAPI middle tier) ---------
    anthropic_api_key: str | None = Field(default=None, validation_alias="ANTHROPIC_API_KEY")
    claude_video_model: str = "claude-opus-4-8"
    # Kept low on purpose: every extra shot is another SLERP morph in the single
    # continuous rollout, and morphing between two different characters turns one
    # face into another. Prefer one sustained take; 2 is the ceiling.
    max_video_shots_per_scene: int = 2

    # Appended verbatim to every shot prompt for a consistent look across shots.
    video_style_suffix: str = (
        "Photorealistic cinematic film still, 35mm lens, dramatic volumetric lighting, "
        "consistent color grade, highly detailed."
    )
    video_negative_prompt: str = (
        "morphing, warping, melting, distortion, flickering, sudden cuts, jump cut, "
        "teleporting, disappearing objects, extra limbs, deformed, mutated, "
        "identity change between shots, color shift between shots, inconsistent lighting, "
        "overexposed, static frame, text, subtitles, watermark, worst quality, low quality, "
        "cartoon, 3d render, cgi, anime"
    )

    # Seconds of video per planned shot.
    render_seconds_per_shot: float = 5.0


settings = RendererSettings()
