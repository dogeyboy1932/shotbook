"""Warm streaming renderer: wraps the vendored Causal-Forcing `StreamingCF` engine.

Loads the Wan2.1-T2V-1.3B autoregressive streaming model ONCE (kept warm in GPU
memory) and renders a ShotBook video plan -- a list of shots, each a fully
assembled text-to-video prompt -- into one stitched mp4. This is the fast
(~16-22 FPS on one H100) replacement for the 14B `generate_video.py` path.

Rendering model (single GPU, see stream_plan):
  - shots are grouped into continuous SEGMENTS split at every 'cut_new_scene';
  - each segment is ONE unbroken streaming rollout (`StreamingCF.start` ->
    `step` -> `decode_chunk`), morphing across same-scene shot changes via
    `ramp_to` so there is no seam within a scene;
  - a 'cut_new_scene' starts a FRESH rollout (new noise + cleared KV/cross-attn/
    VAE caches) = a genuine hard cut, not a morph;
  - all decoded frames are written straight to final_story.mp4 (no ffmpeg stitch).

Real-time engine + attribution: see vendor/cf_streaming.py and NOVELTY.md.
Deferred (see PLAN.md): the 2-GPU DiT/VAE pipeline split.
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
        self._PromptBus = None

    @property
    def is_loaded(self) -> bool:
        return self._pipe is not None

    def load(self) -> None:
        """Load the model once. Blocking + GPU-heavy -- call at service startup.

        cf_streaming.load_cf_pipeline() inserts VENDOR_DIR on sys.path and
        os.chdir()'s into it (the engine references configs/ and the downloaded
        wan_models/ + checkpoints/ by relative path), so weights must live under
        renderer/vendor/{wan_models,checkpoints} (see scripts/setup_renderer.sh).
        """
        if self._pipe is not None:
            return
        if str(VENDOR_DIR) not in sys.path:
            sys.path.insert(0, str(VENDOR_DIR))
        from cf_streaming import PromptBus, StreamingCF, load_cf_pipeline

        t0 = time.time()
        self._pipe = load_cf_pipeline(model=self.model, window=self.window, sink=self.sink)
        self._StreamingCF = StreamingCF
        self._PromptBus = PromptBus
        logger.info("renderer model %r loaded in %.1fs", self.model, time.time() - t0)

    def new_bus(self):
        """A thread-safe live-prompt bus for steering a running stream_plan (#5).
        The frontend POSTs steer text to /jobs/{id}/steer -> bus.set(text); the
        render loop picks it up at the next chunk boundary."""
        if self._PromptBus is None:
            raise RuntimeError("engine not loaded")
        return self._PromptBus("")

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
                    seconds_per_shot: float = DEFAULT_SECONDS_PER_SHOT, bus=None):
        """Generator: render a passage live, yielding each decoded pixel frame as
        it is produced, then writing the final_story.mp4.

        Live steering (#5): if a PromptBus `bus` is given, the user's typed steer
        text is appended to the active shot prompt and SLERP-morphed in whenever
        the bus version changes (and morphed back out when cleared). When the
        user is not typing the bus version is steady, so nothing changes -- the
        passage just renders its planned shots.

        The planner's `continuity` field decides the texture of every shot
        boundary, and we honour it two different ways:
          - within a continuous scene ('continuous_frame' / 'cut_same_scene')
            we keep ONE rollout and SLERP-morph the prompt with ramp_to, so the
            take flows unbroken with no stitch/seam;
          - at a genuine scene break ('cut_new_scene') we do a REAL hard cut by
            starting a FRESH rollout for the next segment -- new noise, cleared
            KV self-attention + cross-attention + VAE caches -- so the new scene
            is visually independent and does not morph out of the old one
            (narrator's face -> a separate insert of the vulture eye, etc).

        So the shots are partitioned into continuous SEGMENTS at every
        cut_new_scene; each segment is its own morphing rollout, and the
        segments are concatenated into the saved mp4.
        """
        if not shots:
            raise ValueError("stream_plan requires at least one shot")
        out = Path(out_dir).resolve()
        out.mkdir(parents=True, exist_ok=True)

        gen = self._StreamingCF(self._pipe, seed=self.seed, window=self.window, sink=self.sink)
        nfpb = gen.nfpb

        # Per-shot latent-frame budgets (snapped to whole chunks).
        budgets: list[int] = []
        for _shot in shots:
            lf = max(nfpb, int(round(seconds_per_shot * LATENT_FPS)))
            lf -= lf % nfpb
            budgets.append(lf)

        prompts = [s.get("prompt") for s in shots]
        if not prompts[0]:
            raise ValueError("shot 0 has no prompt")

        # Partition shot indices into continuous segments. A 'cut_new_scene'
        # (and always shot 0) opens a new segment -> a fresh rollout = hard cut.
        segments: list[list[int]] = []
        for i, shot in enumerate(shots):
            is_cut = i == 0 or (shot.get("continuity") or "").lower() == "cut_new_scene"
            if is_cut or not segments:
                segments.append([i])
            else:
                segments[-1].append(i)

        # Live-steer state persists ACROSS hard cuts: a user modifier ("make it
        # snow") stays applied to every subsequent scene until they change it.
        steer_text = ""
        last_steer_version = 0

        all_frames: list[np.ndarray] = []
        for segment in segments:
            seg_total = sum(budgets[i] for i in segment)
            if not prompts[segment[0]]:           # skip a malformed promptless segment head
                continue

            # latent-frame index WITHIN this segment -> the ramp prompt for an
            # internal (same-scene) shot change.
            seg_boundaries: dict[int, str] = {}
            acc = 0
            for j, i in enumerate(segment[1:], start=1):
                acc += budgets[segment[j - 1]]
                if prompts[i]:
                    seg_boundaries[acc] = prompts[i]

            gen.start(prompts[segment[0]], total_frames=seg_total)  # fresh rollout = hard cut
            self._pipe.vae.model.clear_cache()
            active_base = prompts[segment[0]]
            if steer_text:                        # carry an active steer into the new scene
                gen.ramp_to(_steer_prompt(active_base, steer_text), k=4)

            produced = 0
            for _ in range(seg_total // nfpb):
                if produced in seg_boundaries:    # same-scene shot change -> smooth morph
                    active_base = seg_boundaries[produced]
                    gen.ramp_to(_steer_prompt(active_base, steer_text), k=8)
                elif bus is not None:             # user steer -> morph toward it (faster)
                    steer, version = bus.get()
                    if version != last_steer_version:
                        last_steer_version = version
                        steer_text = steer
                        gen.ramp_to(_steer_prompt(active_base, steer_text), k=4)
                den = gen.step()                  # DiT denoise -> clean latents
                chunk = gen.decode_chunk(den)     # VAE decode -> uint8 [nf,H,W,3]
                produced += nfpb
                for frame in chunk:
                    all_frames.append(frame)
                    yield frame

        if all_frames:
            final = out / "final_story.mp4"
            imageio.mimwrite(str(final), np.stack(all_frames), fps=FPS,
                             codec="libx264", macro_block_size=1)
            logger.info("saved render -> %s (%d frames, %d shots, %d segments)",
                        final, len(all_frames), len(shots), len(segments))


def _steer_prompt(base: str, steer: str) -> str:
    """Combine the active planned shot prompt with the user's live steer text
    (#5). Empty steer -> the unmodified planned prompt (steady state)."""
    steer = (steer or "").strip()
    return f"{base} {steer}" if steer else base


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
