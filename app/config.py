"""Centralized runtime configuration, loaded from environment variables.

A single Settings object is shared by both the FastAPI service and the
ingestion orchestrator so the two never disagree about how to reach Postgres
or the vLLM fleet.
"""
from __future__ import annotations

from pydantic import Field
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=".env", env_prefix="BVG_", extra="ignore")

    # --- PostgreSQL -----------------------------------------------------
    database_url: str = "postgresql+asyncpg://postgres:postgres@localhost:5432/book_video_gen"
    db_pool_size: int = 20
    db_max_overflow: int = 10

    # --- vLLM cluster (8x H100, one OpenAI-compatible server per GPU) ---
    # Each entry is the base URL of an independent vLLM server instance.
    # Running one replica per GPU (rather than a single tensor-parallel
    # server) is what gives us *data* parallelism across paragraphs/chunks.
    vllm_endpoints: list[str] = [
        "http://gpu-0:8000/v1",
        "http://gpu-1:8000/v1",
        "http://gpu-2:8000/v1",
        "http://gpu-3:8000/v1",
        "http://gpu-4:8000/v1",
        "http://gpu-5:8000/v1",
        "http://gpu-6:8000/v1",
        "http://gpu-7:8000/v1",
    ]
    vllm_model_name: str = "meta-llama/Meta-Llama-3-70B-Instruct"
    vllm_request_timeout_s: float = 120.0
    vllm_max_retries: int = 3

    # --- Ingestion tuning -------------------------------------------------
    concurrent_requests_per_gpu: int = 4       # in-flight requests per vLLM replica
    paragraph_chunk_size: int = 8               # paragraphs grouped into one LLM call during Pass 2

    # --- Video shot planning (app/video_prompting.py) ----------------------
    # Appended verbatim to every generated shot prompt so all clips composited
    # into one scene share a consistent look -- the same role a hand-written
    # {STYLE} suffix plays in a manually authored SCENES list.
    video_style_suffix: str = (
        "Photorealistic cinematic film still, 35mm lens, dramatic volumetric lighting, "
        "consistent color grade, highly detailed."
    )
    # Same idea as video_style_suffix but for what NOT to generate -- fixed and
    # applied to every shot so failure modes (identity drift, lighting jumps
    # between clips, AI-art artifacts) are suppressed consistently everywhere.
    video_negative_prompt: str = (
        "morphing, warping, melting, distortion, flickering, sudden cuts, jump cut, "
        "teleporting, disappearing objects, extra limbs, deformed, mutated, "
        "identity change between shots, color shift between shots, inconsistent lighting, "
        "overexposed, static frame, text, subtitles, watermark, worst quality, low quality, "
        "cartoon, 3d render, cgi, anime"
    )
    max_video_shots_per_scene: int = 4

    # --- Claude API (compose-scene's video shot planning only --
    # book ingestion stays on the local vLLM fleet above) ------------------
    # Reads the SDK's own conventional env var name (no BVG_ prefix) so a
    # plain `export ANTHROPIC_API_KEY=...` just works.
    anthropic_api_key: str | None = Field(default=None, validation_alias="ANTHROPIC_API_KEY")
    claude_video_model: str = "claude-opus-4-8"


settings = Settings()
