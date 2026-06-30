"""Warm 5B quality renderer (Wan2.2-TI2V-5B via diffusers) -- the 'HD' path.

The cinematic half of the hybrid: a separate warm service (own venv
.venv-quality, newer diffusers) that the backend routes to when a job asks for
quality=true. Mirrors RenderEngine's interface (load / render_plan ->
(final, total_frames)) so app/routers/video_jobs.py can treat the fast 1.3B
streaming renderer and this 5B renderer interchangeably -- only the URL differs.

This is one-shot diffusion: ~2 min/shot at 720p, no streaming / real-time
prompt injection. Use the 1.3B renderer for the live "frames as you read"
preview; use this for the polished final render.
"""
from __future__ import annotations

import logging
import shutil
import time
from pathlib import Path

import torch
from diffusers import AutoencoderKLWan, WanPipeline
from diffusers.utils import export_to_video

# Reuse the exact same stitch step the fast renderer uses (pure ffmpeg; importing
# renderer.py does NOT pull in the 1.3B/flash-attn stack -- that's lazy-loaded
# inside RenderEngine.load, so this is safe in the quality venv).
from renderer.renderer import _ffmpeg_concat

logger = logging.getLogger("quality")

MODEL = "wan_models/Wan2.2-TI2V-5B-Diffusers"
FPS = 24
DEFAULT_SECONDS_PER_SHOT = 5.0
NEG = ("blurry, low quality, distorted, deformed, extra limbs, bad anatomy, "
       "watermark, text, cartoon, 3d render, flickering, morphing")


def _frames_for(seconds: float) -> int:
    """Wan 5B wants 4k+1 frames @ 24fps; clamp to the model's ~5s sweet spot."""
    n = int(round(seconds * FPS))
    n = max(25, min(n, 121))
    n -= (n - 1) % 4            # snap down to the nearest 4k+1
    return n


class QualityEngine:
    """Holds the warm 5B pipeline and renders plans against it."""

    def __init__(self, height: int = 704, width: int = 1280, steps: int = 40,
                 guidance: float = 5.0, seed: int = 42):
        self.height, self.width = height, width
        self.steps, self.guidance, self.seed = steps, guidance, seed
        self.model = "wan2.2-5b"
        self._pipe = None

    @property
    def is_loaded(self) -> bool:
        return self._pipe is not None

    def load(self) -> None:
        """Load the 5B model once. Blocking + GPU-heavy -- call at startup."""
        if self._pipe is not None:
            return
        t0 = time.time()
        vae = AutoencoderKLWan.from_pretrained(MODEL, subfolder="vae", torch_dtype=torch.float32)
        pipe = WanPipeline.from_pretrained(MODEL, vae=vae, torch_dtype=torch.bfloat16)
        pipe.to("cuda")
        self._pipe = pipe
        logger.info("quality model loaded in %.1fs", time.time() - t0)

    def _render_shot(self, prompt: str, seconds: float, out_path: Path,
                     negative_prompt: str | None, steps: int | None = None,
                     guidance: float | None = None) -> int:
        nframes = _frames_for(seconds)
        g = torch.Generator(device="cuda").manual_seed(self.seed)
        frames = self._pipe(
            prompt=prompt, negative_prompt=negative_prompt or NEG,
            height=self.height, width=self.width, num_frames=nframes,
            num_inference_steps=steps or self.steps,
            guidance_scale=self.guidance if guidance is None else guidance,
            generator=g,
        ).frames[0]
        export_to_video(frames, str(out_path), fps=FPS)
        return len(frames)

    def render_plan(self, shots: list[dict], out_dir: str,
                    seconds_per_shot: float = DEFAULT_SECONDS_PER_SHOT,
                    negative_prompt: str | None = None,
                    steps: int | None = None, guidance: float | None = None) -> tuple[Path, int]:
        """Render every shot at 720p, stitch into final_story.mp4."""
        if not shots:
            raise ValueError("render_plan requires at least one shot")
        out = Path(out_dir).resolve()
        out.mkdir(parents=True, exist_ok=True)

        shot_files: list[Path] = []
        total_frames = 0
        for i, shot in enumerate(shots):
            prompt = shot.get("prompt")
            if not prompt:
                raise ValueError(f"shot {i} has no prompt")
            slug = (shot.get("shot_id") or f"{i + 1:02d}").replace(" ", "_")
            shot_path = out / f"shot_{i:02d}_{slug}.mp4"
            n = self._render_shot(prompt, seconds_per_shot, shot_path, negative_prompt, steps, guidance)
            total_frames += n
            shot_files.append(shot_path)
            logger.info("HD shot %d/%d %r: %d frames -> %s",
                        i + 1, len(shots), slug, n, shot_path.name)

        final = out / "final_story.mp4"
        if len(shot_files) == 1:
            shutil.copy(shot_files[0], final)
        else:
            _ffmpeg_concat(shot_files, final)
        logger.info("stitched %d HD shot(s) -> %s", len(shot_files), final)
        return final, total_frames
