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
    # Shot planning is a heavier structured task where plan quality matters, so it
    # stays on a capable model. Live-steer merges are small, frequent, and latency-
    # sensitive -> a fast/cheap model (Haiku) keeps steering snappy and low-cost.
    claude_video_model: str = "claude-opus-4-8"
    claude_fast_model: str = "claude-haiku-4-5-20251001"
    # A highlighted passage is rendered as a SEQUENCE of beats -- one shot per
    # distinct action, fed to the model in order -- so a multi-action passage
    # ("leaves the house, gets in the car, drives away") plays out sequentially
    # instead of blending into one scene. This caps how many beats a single
    # passage may become (~render_seconds_per_shot each -> up to ~25s).
    max_video_shots_per_scene: int = 5

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

    # Safety ceiling on TOTAL generated frames per live session (seconds of
    # video). In takeover the user steers freely and the render only composes on
    # Finish; this just bounds how many frames all the bursts can sum to (noise
    # buffer is preallocated to it). Holding between steers is free and unbounded.
    # Generous so steering feels open-ended; raise if you need longer sessions.
    max_session_seconds: float = 180.0

    # Real-time only: after the planned beats finish, hold on the last frame for
    # this long (a visible countdown) BEFORE composing, so the user can add on
    # (steer) to keep generating. If they skip, it composes now; if the countdown
    # lapses untouched, it drops to an indefinite pause (nothing saved) until they
    # steer or finish.
    realtime_buffer_seconds: float = 10.0

    # Max number of steers a user may queue in one takeover session. Bounds the
    # total frames a session can generate (each steer renders one scene), so a
    # single job can't hold the GPU forever.
    max_steers_per_session: int = 10

    # _SPED "window-shrink" edit-strength knob: how many self-attn frames the
    # model reads right after a steer. Smaller flushes old-attribute momentum
    # faster so the change takes harder; too small (~1-3) drops subject coherence
    # and flickers. ~6 is a moderate balance for an attribute edit (keep the
    # character, change the hat). Larger resists (change barely shows).
    realtime_steer_window: int = 6
    # Chunks over which a steer SLERP-morphs old->new. Higher = smoother/slower.
    realtime_steer_ramp_chunks: int = 4


settings = RendererSettings()
