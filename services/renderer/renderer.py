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
        """Render the whole passage as ONE seamless rollout; save final_story.mp4.

        Delegates to stream_plan (the single continuous rollout) and drains it,
        so the saved mp4 is exactly what the live stream shows -- no per-shot
        clips, no ffmpeg stitch, no seams.
        """
        count = 0
        for _ in self.stream_plan(shots, out_dir, seconds_per_shot):
            count += 1
        final = Path(out_dir).resolve() / "final_story.mp4"
        return final, count

    def stream_plan(self, shots: list[dict], out_dir: str,
                    seconds_per_shot: float = DEFAULT_SECONDS_PER_SHOT):
        """Generator: render a whole passage as ONE unbroken autoregressive
        rollout, yielding each decoded pixel frame live as it is produced, then
        writing the continuous final_story.mp4.

        This is the seamless, non-stitched path. Instead of a fresh rollout per
        shot (which leaves a visible seam at every cut), we start a SINGLE
        rollout sized for all shots and TRANSITION the prompt at each shot
        boundary while the KV-cache carries the visual state forward -- the
        engine's own hardcut/ramp_to pattern (cf_streaming.py __main__):
          - continuity 'cut_new_scene' -> hardcut(prompt)  (swap conditioning)
          - anything else              -> ramp_to(prompt)  (smooth SLERP morph)
        so frames flow one into the next with no stitch.
        """
        if not shots:
            raise ValueError("stream_plan requires at least one shot")
        out = Path(out_dir).resolve()
        out.mkdir(parents=True, exist_ok=True)

        gen = self._StreamingCF(self._pipe, seed=self.seed, window=self.window, sink=self.sink)
        nfpb = gen.nfpb

        # Per-shot latent-frame budgets (snapped to whole chunks), summed into one
        # continuous rollout length.
        budgets: list[int] = []
        for shot in shots:
            lf = max(nfpb, int(round(seconds_per_shot * LATENT_FPS)))
            lf -= lf % nfpb
            budgets.append(lf)
        total = sum(budgets)

        prompts = [s.get("prompt") for s in shots]
        if not prompts[0]:
            raise ValueError("shot 0 has no prompt")

        # latent-frame index where each later shot begins -> (prompt, continuity)
        boundaries: dict[int, tuple[str, str]] = {}
        acc = 0
        for i in range(1, len(shots)):
            acc += budgets[i - 1]
            if prompts[i]:
                boundaries[acc] = (prompts[i], (shots[i].get("continuity") or "").lower())

        gen.start(prompts[0], total_frames=total)
        self._pipe.vae.model.clear_cache()

        all_frames: list[np.ndarray] = []
        produced = 0
        for _ in range(total // nfpb):
            if produced in boundaries:           # entering a new shot -> transition prompt
                new_prompt, continuity = boundaries[produced]
                # A single rollout is one continuously-morphing shot: the KV-cache
                # keeps the picture flowing, so a hardcut to an UNRELATED prompt
                # doesn't cut -- it hallucinates the new prompt into the old frame.
                # For seamlessness we always SLERP-morph; a brisker ramp for a
                # declared scene change, a gentler one to hold continuity.
                k = 4 if continuity == "cut_new_scene" else 8
                gen.ramp_to(new_prompt, k=k)
            den = gen.step()                     # DiT denoise -> clean latents
            chunk = gen.decode_chunk(den)        # VAE decode -> uint8 [nf,H,W,3]
            produced += nfpb
            for frame in chunk:
                all_frames.append(frame)
                yield frame

        if all_frames:
            final = out / "final_story.mp4"
            imageio.mimwrite(str(final), np.stack(all_frames), fps=FPS,
                             codec="libx264", macro_block_size=1)
            logger.info("saved seamless render -> %s (%d frames, %d shots)",
                        final, len(all_frames), len(shots))


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
