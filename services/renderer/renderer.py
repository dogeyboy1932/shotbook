"""Warm streaming renderer: wraps the vendored Causal-Forcing `StreamingCF` engine.

Loads the Wan2.1-T2V-1.3B autoregressive streaming model ONCE (kept warm in GPU
memory) and renders a ShotBook video plan -- a list of shots, each a fully
assembled text-to-video prompt -- into one stitched mp4. This is the fast
(~16-22 FPS on one H100) replacement for the 14B `generate_video.py` path.

PoC scope (single GPU, stitched shots):
  - each shot is rendered as an independent <=5s clip via the streaming loop
    (`StreamingCF.start` -> `step` -> `decode_chunk`), exactly like the engine's
    own smoke test in vendor/cf_streaming.py;
  - shots are ffmpeg-concatenated into final_story.mp4 (same stitch step the old
    generate_video.py did).

Deferred (see PLAN.md): the 2-GPU DiT/VAE pipeline split, and rendering a whole
scene as ONE unbroken stream via mid-generation prompt-swap (hardcut/ramp_to)
driven by each shot's `continuity` field.
"""
from __future__ import annotations

import logging
import os
import shutil
import subprocess
import sys
import time
from pathlib import Path

import imageio
import imageio_ffmpeg
import numpy as np

logger = logging.getLogger("renderer")

VENDOR_DIR = Path(__file__).resolve().parent / "vendor"

FPS = 16                     # pixel-frame rate of the model's output
LATENT_FPS = 4               # 1 latent frame -> 4 pixel frames (Wan VAE 4x temporal)
DEFAULT_SECONDS_PER_SHOT = 5.0

# Motion-smoothing post-process: interpolate the 16fps model output up to this fps
# with ffmpeg's motion-compensated interpolation -- the biggest cheap win for
# "more realistic animation". No extra deps, no API cost. 0 disables.
INTERP_FPS = int(os.getenv("RENDER_INTERP_FPS", "32"))


class RenderEngine:
    """Holds the warm streaming pipeline and renders plans against it."""

    def __init__(self, model: str = "chunkwise", window: int = 21, sink: int = 3, seed: int = 42):
        self.model = model
        self.window = window
        self.sink = sink
        self.seed = seed
        self._pipe = None
        self._StreamingCF = None

    @property
    def is_loaded(self) -> bool:
        return self._pipe is not None

    def load(self) -> None:
        """Load the model once. Blocking + GPU-heavy -- call at service startup.

        cf_streaming.load_cf_pipeline() inserts VENDOR_DIR on sys.path and
        os.chdir()'s into it (the engine references configs/ and the downloaded
        wan_models/ + checkpoints/ by relative path), so weights must live under
        services/renderer/vendor/{wan_models,checkpoints} (see scripts/setup_renderer.sh).
        """
        if self._pipe is not None:
            return
        if str(VENDOR_DIR) not in sys.path:
            sys.path.insert(0, str(VENDOR_DIR))
        from cf_streaming import StreamingCF, load_cf_pipeline

        t0 = time.time()
        self._pipe = load_cf_pipeline(model=self.model, window=self.window, sink=self.sink)
        self._StreamingCF = StreamingCF
        logger.info("renderer model %r loaded in %.1fs", self.model, time.time() - t0)

    def _render_shot(self, prompt: str, seconds: float, out_path: Path) -> int:
        """Stream one shot to an mp4; returns the pixel-frame count."""
        gen = self._StreamingCF(self._pipe, seed=self.seed, window=self.window, sink=self.sink)
        # total = latent frames; ~4 latent frames/sec, snapped to a whole number of chunks.
        total = max(gen.nfpb, int(round(seconds * LATENT_FPS)))
        total -= total % gen.nfpb
        gen.start(prompt, total_frames=total)
        self._pipe.vae.model.clear_cache()  # fresh streaming VAE decode per shot

        frames = []
        for _ in range(total // gen.nfpb):
            den = gen.step()                 # DiT denoise -> clean latents (compute-heavy)
            frames.append(gen.decode_chunk(den))  # VAE decode -> uint8 [nf,H,W,3]
        frames = np.concatenate(frames, axis=0)
        imageio.mimwrite(str(out_path), frames, fps=FPS, codec="libx264", macro_block_size=1)
        return int(frames.shape[0])

    def render_plan(
        self, shots: list[dict], out_dir: str, seconds_per_shot: float = DEFAULT_SECONDS_PER_SHOT
    ) -> tuple[Path, int]:
        """Render every shot, stitch into final_story.mp4. Returns (path, total_frames)."""
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
            n = self._render_shot(prompt, seconds_per_shot, shot_path)
            total_frames += n
            # Smooth the motion per-shot (before stitching, so cuts aren't interpolated across).
            if INTERP_FPS:
                smooth = out / f"shot_{i:02d}_{slug}_smooth.mp4"
                _interpolate(shot_path, smooth, INTERP_FPS)
                shot_files.append(smooth)
            else:
                shot_files.append(shot_path)
            logger.info("shot %d/%d %r: %d frames%s -> %s", i + 1, len(shots), slug, n,
                        f" (interp->{INTERP_FPS}fps)" if INTERP_FPS else "", shot_path.name)

        final = out / "final_story.mp4"
        if len(shot_files) == 1:
            shutil.copy(shot_files[0], final)
        else:
            _ffmpeg_concat(shot_files, final)
        logger.info("stitched %d shot(s) -> %s", len(shot_files), final)
        return final, total_frames

    def stream_plan(self, shots: list[dict], out_dir: str,
                    seconds_per_shot: float = DEFAULT_SECONDS_PER_SHOT):
        """Generator: yield each decoded pixel frame (uint8 HxWx3 RGB) live as it
        is produced across all shots, then write final_story.mp4 so the finished
        clip is still saved.

        This is the "frames as they generate" path. The streaming model produces
        frames chunk-by-chunk from a single prompt per shot (see PLAN.md), so the
        caller can push each frame to the browser the moment it exists instead of
        waiting for the whole render + stitch.
        """
        if not shots:
            raise ValueError("stream_plan requires at least one shot")
        out = Path(out_dir).resolve()
        out.mkdir(parents=True, exist_ok=True)

        all_frames: list[np.ndarray] = []
        for i, shot in enumerate(shots):
            prompt = shot.get("prompt")
            if not prompt:
                raise ValueError(f"shot {i} has no prompt")
            gen = self._StreamingCF(self._pipe, seed=self.seed, window=self.window, sink=self.sink)
            total = max(gen.nfpb, int(round(seconds_per_shot * LATENT_FPS)))
            total -= total % gen.nfpb
            gen.start(prompt, total_frames=total)
            self._pipe.vae.model.clear_cache()
            for _ in range(total // gen.nfpb):
                den = gen.step()                 # DiT denoise -> clean latents
                chunk = gen.decode_chunk(den)    # VAE decode -> uint8 [nf,H,W,3]
                for frame in chunk:
                    all_frames.append(frame)
                    yield frame
            logger.info("streamed shot %d/%d (%d frames so far)", i + 1, len(shots), len(all_frames))

        if all_frames:
            final = out / "final_story.mp4"
            imageio.mimwrite(str(final), np.stack(all_frames), fps=FPS,
                             codec="libx264", macro_block_size=1)
            logger.info("saved streamed render -> %s (%d frames)", final, len(all_frames))


def _ffmpeg_concat(clips: list[Path], final: Path) -> None:
    """Concat clips into one continuous mp4 (same approach as generate_video.py)."""
    ffmpeg = imageio_ffmpeg.get_ffmpeg_exe()
    cmd = [ffmpeg, "-y"]
    for c in clips:
        cmd += ["-i", str(c)]
    n = len(clips)
    streams = "".join(f"[{i}]" for i in range(n))
    cmd += [
        "-filter_complex", f"{streams}concat=n={n}:v=1:a=0[v]",
        "-map", "[v]", "-c:v", "libx264", "-crf", "18", "-movflags", "+faststart",
        str(final),
    ]
    subprocess.run(cmd, check=True, capture_output=True)


def _interpolate(src: Path, dst: Path, fps: int) -> None:
    """Motion-compensated frame interpolation up to `fps` -- smooths the choppy
    16fps output into more fluid motion. ffmpeg-native (no extra deps)."""
    ffmpeg = imageio_ffmpeg.get_ffmpeg_exe()
    vf = f"minterpolate=fps={fps}:mi_mode=mci:mc_mode=aobmc:me_mode=bidir:vsbmc=1"
    subprocess.run(
        [ffmpeg, "-y", "-i", str(src), "-vf", vf,
         "-c:v", "libx264", "-crf", "18", "-pix_fmt", "yuv420p", "-movflags", "+faststart", str(dst)],
        check=True, capture_output=True,
    )
