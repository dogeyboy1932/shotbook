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

Real-time engine + attribution: see vendor/cf_streaming.py and _docs/NOVELTY.md.
Deferred (see PLAN.md): the 2-GPU DiT/VAE pipeline split.
"""
from __future__ import annotations

import logging
import os
import shutil
import subprocess
import sys
import threading
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


class RenderControl:
    """Per-job live control the render loop polls each chunk (real-time mode).

    Two interaction modes drive a job, published as `phase` for the /jobs/{id} poll:

      - "running"   -- the planned beats are generating in order.
      - "buffering" -- planned beats finished; a countdown runs before composing.
                       The user can Skip (compose now), Pause/Resume the countdown
                       (buy time), or Takeover. If it lapses untouched -> compose.
      - "takeover"  -- the user took control: each Steer is QUEUED into the
                       diffusion model and rendered in order (one scene per
                       steer); between/after the queued steers the rollout HOLDS
                       on the last frame. The ONLY thing that composes is Finish.
                       Steers are capped at `max_steers`.

    Thread-safe: set from the FastAPI endpoints, read from the render thread.
    Built on the same idea as the vendored PromptBus (see _docs/NOVELTY.md)."""

    def __init__(self, max_steers: int = 10) -> None:
        self._lock = threading.Lock()
        self._queue: list[str] = []  # pending steer prompts (FIFO), drained by the loop
        self._plan: list | None = None  # refined shots from the async planner (Phase 7)
        self._steers_used = 0        # how many steers have been accepted this session
        self._max_steers = max(0, int(max_steers))
        self._takeover = False       # user pressed Takeover
        self._finished = False       # user pressed Finish / Skip (compose now)
        self._phase = "running"
        # countdown (buffering phase only)
        self._cd_deadline: float | None = None
        self._cd_remaining: float | None = None
        self._cd_paused = False

    # -- user actions (called from the endpoints) ----------------------------
    def request_takeover(self) -> None:
        with self._lock:
            self._takeover = True

    def enqueue_steer(self, prompt: str) -> tuple[bool, int]:
        """Queue one steer. Returns (accepted, steers_remaining). Rejected when
        empty or the per-session cap is reached."""
        with self._lock:
            p = (prompt or "").strip()
            if not p or self._steers_used >= self._max_steers:
                return False, max(0, self._max_steers - self._steers_used)
            self._queue.append(p)
            self._steers_used += 1
            return True, self._max_steers - self._steers_used

    def pop_steer(self) -> str | None:
        """Engine-side: next queued steer prompt, or None if the queue is empty."""
        with self._lock:
            return self._queue.pop(0) if self._queue else None

    # -- live PLANNED shots (Phase 7): async planner -> render thread ----------
    def push_plan(self, shots: list) -> None:
        """Producer (background Claude planner): hand the refined shot list to the
        running render so it can morph off the bootstrap into the real plan."""
        with self._lock:
            self._plan = shots

    def pop_plan(self):
        """Consumer (render thread): the refined shots once, then None. Returns
        the list exactly one time so the PLANNED phase picks it up at a boundary."""
        with self._lock:
            plan, self._plan = self._plan, None
            return plan

    def finish(self) -> None:
        with self._lock:
            self._finished = True

    def pause_countdown(self) -> None:
        with self._lock:
            if self._phase == "buffering" and not self._cd_paused and self._cd_deadline is not None:
                self._cd_remaining = max(0.0, self._cd_deadline - time.monotonic())
                self._cd_paused = True

    def resume_countdown(self) -> None:
        with self._lock:
            if self._phase == "buffering" and self._cd_paused:
                self._cd_deadline = time.monotonic() + (self._cd_remaining or 0.0)
                self._cd_paused = False

    # -- engine-side transitions + reads -------------------------------------
    def enter_buffer(self, seconds: float) -> None:
        with self._lock:
            self._phase = "buffering"
            self._cd_deadline = time.monotonic() + seconds
            self._cd_remaining = seconds
            self._cd_paused = False

    def enter_takeover(self) -> None:
        with self._lock:
            self._phase = "takeover"
            self._takeover = True
            self._cd_deadline = self._cd_remaining = None

    def countdown_lapsed(self) -> bool:
        with self._lock:
            return (self._phase == "buffering" and not self._cd_paused
                    and self._cd_deadline is not None
                    and time.monotonic() >= self._cd_deadline)

    def flags(self) -> tuple[bool, bool]:
        """(takeover_requested, finished) -- polled by the render loop."""
        with self._lock:
            return self._takeover, self._finished

    def phase_info(self) -> tuple[str, float | None, int]:
        """(phase, countdown_seconds_remaining, steers_remaining)."""
        with self._lock:
            remaining = None
            if self._phase == "buffering":
                remaining = (self._cd_remaining if self._cd_paused
                             else max(0.0, (self._cd_deadline or 0.0) - time.monotonic()))
            return self._phase, remaining, max(0, self._max_steers - self._steers_used)

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
        renderer/vendor/{wan_models,checkpoints} (see scripts/setup_renderer.sh).
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
                    seconds_per_shot: float = DEFAULT_SECONDS_PER_SHOT, control=None,
                    max_session_frames: int | None = None, buffer_seconds: float = 0.0,
                    steer_window: int = 6, steer_ramp: int = 4, await_plan: bool = False):
        """Generator: render a passage live as a SEQUENCE of beats, yielding each
        decoded pixel frame as it is produced, then writing final_story.mp4.

        Phases:

        PLANNED -- the beats Claude planned, in order (one shot per action) so a
        multi-action passage reads sequentially instead of blending. The
        `continuity` field shapes each boundary: within a scene we keep ONE
        rollout and SLERP-morph with ramp_to (no seam); at a 'cut_new_scene' we
        start a FRESH rollout (new noise + cleared KV/cross-attn/VAE caches) = a
        real hard cut.

        LIVE TAKEOVER -- as soon as the user STEERS, the SAME rollout keeps going
        OPEN-ENDED toward their prompt (sized to `max_session_frames`), so steering
        is NOT capped to the planned length. Each further steer is a smooth
        ramp_to; FINISH (or the session ceiling) ends it and saves.

        BUFFER + HOLD -- if the planned beats finish without a takeover and
        `buffer_seconds` > 0, we hold on the last frame with a countdown (control
        phase 'buffering') so the user can still add on; a late steer resumes the
        SAME rollout open-ended. If the countdown lapses untouched it drops to an
        indefinite 'paused' hold (nothing saved). FINISH composes; otherwise we
        keep the MJPEG stream alive by re-emitting the last frame (so a browser
        disconnect frees the GPU). Real-time mode only; pass a RenderControl.
        """
        if not shots:
            raise ValueError("stream_plan requires at least one shot")
        out = Path(out_dir).resolve()
        out.mkdir(parents=True, exist_ok=True)

        gen = self._StreamingCF(self._pipe, seed=self.seed, window=self.window, sink=self.sink)
        nfpb = gen.nfpb

        prompts = [s.get("prompt") for s in shots]
        if not prompts[0]:
            raise ValueError("shot 0 has no prompt")

        all_frames: list[np.ndarray] = []
        last_frame: np.ndarray | None = None

        # Each rollout is allocated to the SESSION CEILING up front (not just its
        # planned budget): the noise is seeded deterministically, so the planned
        # beats render identically regardless of allocated length, but there's
        # headroom to keep stepping the SAME rollout if the user takes over.
        ceiling = max_session_frames if max_session_frames else int(round(60.0 * LATENT_FPS))
        ceiling -= ceiling % nfpb
        ceiling = max(ceiling, nfpb)
        # Frames generated per Steer in takeover -- one steer's scene, then hold.
        burst_steps = max(1, (max(nfpb, int(round(seconds_per_shot * LATENT_FPS))) // nfpb))

        def flags():
            return control.flags() if control is not None else (False, False)

        def emit():
            nonlocal last_frame
            for frame in gen.decode_chunk(gen.step()):
                last_frame = frame
                all_frames.append(frame)
                yield frame

        finished = False
        took_over = False

        def render_shot_list(shot_list, morph_first):
            """Render a shots list segment-by-segment: same-scene beats ramp_to
            (seamless), a 'cut_new_scene' beat starts a FRESH rollout (real hard
            cut). If morph_first, the FIRST segment ramps from the CURRENT running
            rollout instead of a fresh start -- the seamless bootstrap->plan morph
            (Phase 7). Sets finished/took_over on early break; yields frames."""
            nonlocal finished, took_over
            b = []
            for _ in shot_list:
                lf = max(nfpb, int(round(seconds_per_shot * LATENT_FPS)))
                lf -= lf % nfpb
                b.append(lf)
            p = [s.get("prompt") for s in shot_list]
            segs: list[list[int]] = []
            for idx, sh in enumerate(shot_list):
                if idx == 0 or (sh.get("continuity") or "").lower() == "cut_new_scene":
                    segs.append([idx])
                else:
                    segs[-1].append(idx)
            for si, segment in enumerate(segs):
                if finished or took_over:
                    return
                head = p[segment[0]]
                if not head:
                    continue
                seg_total = sum(b[i] for i in segment)
                seg_boundaries: dict[int, str] = {}
                acc = 0
                for j, i in enumerate(segment[1:], start=1):
                    acc += b[segment[j - 1]]
                    if p[i]:
                        seg_boundaries[acc] = p[i]
                if morph_first and si == 0:
                    gen.ramp_to(head, k=8)         # seamless morph from the running rollout
                else:
                    gen.start(head, total_frames=ceiling)  # fresh rollout = hard cut
                    self._pipe.vae.model.clear_cache()
                produced = 0
                while produced < seg_total:
                    takeover_req, fin = flags()
                    if fin:
                        finished = True
                        return
                    if takeover_req:
                        took_over = True
                        return
                    if produced in seg_boundaries:
                        gen.ramp_to(seg_boundaries[produced], k=8)
                    produced += nfpb
                    yield from emit()

        # ---- PLANNED phase ----------------------------------------------------
        if await_plan and control is not None:
            # Phase 7: `shots` is the deterministic BOOTSTRAP (one shot). Start it
            # instantly for a ~1-2s first frame, hold on it while the refined plan
            # is composed in the background, then morph seamlessly into that plan.
            gen.start(prompts[0], total_frames=ceiling)
            self._pipe.vae.model.clear_cache()
            refined = None
            produced = 0
            while produced < ceiling and not finished and not took_over:
                takeover_req, fin = flags()
                if fin:
                    finished = True
                    break
                if takeover_req:
                    took_over = True
                    break
                refined = control.pop_plan()
                if refined:
                    break
                produced += nfpb
                yield from emit()
            if refined and not finished and not took_over:
                yield from render_shot_list(refined, morph_first=True)
        else:
            # Non-interactive / no live planner: render the given shots directly.
            yield from render_shot_list(shots, morph_first=False)

        # ---- BUFFER phase: countdown before composing (planned beats finished) -
        if control is not None and not took_over and not finished and buffer_seconds > 0:
            control.enter_buffer(buffer_seconds)
            while True:
                takeover_req, fin = flags()
                if fin:                           # Skip -> compose now
                    finished = True
                    break
                if takeover_req:                  # Takeover -> steer mode
                    took_over = True
                    break
                if control.countdown_lapsed():    # lapsed untouched -> compose
                    break
                if last_frame is not None:        # keep the stream alive while holding
                    yield last_frame
                time.sleep(0.4)

        # ---- TAKEOVER phase: the ONLY exit is FINISH; otherwise hold forever ---
        # Steers are QUEUED and drained in order. Each is a DECISIVE swap via the
        # _SPED window-shrink method (gen.steer): a plain prompt swap resists
        # because the self-attn KV cache holds the old scene, so we hardcut the
        # conditioning AND shrink the read window so old frames flush fast and the
        # new prompt actually takes ("baseball cap" shows up). We hold the shrunk
        # window through the whole takeover for responsive steers, then restore it
        # for a final settle before composing. Nothing composes on its own; only
        # Finish breaks the loop.
        if took_over and not finished:
            control.enter_takeover()
            # Long enough for the shrunk window to flush the old scene + establish
            # the new one (transition time ~= window-flush time).
            steer_burst = max(burst_steps, (gen.window // nfpb) + 2)
            steered = False
            while not finished:
                _t, fin = flags()
                if fin:                           # Finish -> compose (the only way out)
                    break
                nxt = control.pop_steer() if control is not None else None
                if nxt is not None and gen.cur_frame < ceiling:
                    gen.steer(nxt, shrink_window=steer_window, k=steer_ramp)  # _SPED continuous edit
                    steered = True
                    for _ in range(steer_burst):  # flush + establish the new scene
                        if gen.cur_frame >= ceiling:
                            break
                        _t2, fin2 = flags()
                        if fin2:
                            finished = True
                            break
                        yield from emit()
                else:                             # holding -- standby for next Steer/Finish
                    if last_frame is not None:
                        yield last_frame
                    time.sleep(0.4)
            if steered:
                gen.restore_window()              # coherent settle before compose

        if all_frames:
            final = out / "final_story.mp4"
            imageio.mimwrite(str(final), np.stack(all_frames), fps=FPS,
                             codec="libx264", macro_block_size=1)
            logger.info("saved render -> %s (%d frames, await_plan=%s, takeover=%s)",
                        final, len(all_frames), await_plan, took_over)


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
