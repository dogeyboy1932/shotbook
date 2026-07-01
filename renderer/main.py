"""Renderer microservice (FastAPI) — the single VM backend.

Loads the streaming video model ONCE at startup (warm) and now also owns shot
planning (Claude) so the React frontend talks only to Supabase (data) and this
service (plan + render). The old FastAPI middle tier (app/) is retired.

High-level flow used by the frontend:
    POST /generate {contexts}        -> plan shots (Claude); returns {job_id, scene}
    GET  /jobs/{id}                  -> {status, stream_url, video_url, error}
    GET  /jobs/{id}/stream           -> live MJPEG (frames appear as generated)
    GET  /jobs/{id}/video            -> the finished mp4

Low-level /render and /render/stream (POST a raw shot list) are kept for testing.

    CUDA_VISIBLE_DEVICES=0 .venv-renderer/bin/uvicorn renderer.main:app \
        --host 0.0.0.0 --port 8004
"""
from __future__ import annotations

import asyncio
import logging
import shutil
import threading
import time
import uuid
from contextlib import asynccontextmanager
from datetime import datetime
from pathlib import Path

import cv2
from fastapi import FastAPI, HTTPException
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse, StreamingResponse
from pydantic import BaseModel

from renderer.config import settings
from renderer.ingest import router as ingest_router
from renderer.planning import (
    VideoPlanningError,
    bootstrap_plan,
    compose_scene,
    compose_steer_prompt,
    generate_video_plan,
)
from renderer.renderer import RenderControl, RenderEngine
from renderer.schema import RenderRequest, RenderResponse
from renderer.schemas import GenerateRequest

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger("renderer.main")

# Model variant is a config knob (BVG_RENDERER_MODEL); see renderer/config.py.
engine = RenderEngine(model=settings.renderer_model)

# Single GPU, single model instance -> serialize renders.
_render_lock = asyncio.Lock()
# The live MJPEG endpoints run in worker threads (sync generators).
_stream_lock = threading.Lock()

PROJECT_ROOT = Path(__file__).resolve().parent.parent.parent
JOBS_DIR = PROJECT_ROOT / "video_jobs"
JOBS_DIR.mkdir(exist_ok=True)
OUTPUT_DIR = PROJECT_ROOT / "generated_videos"
OUTPUT_DIR.mkdir(exist_ok=True)

# job_id -> {status, plan, scene, video_path, error}
_jobs: dict[str, dict] = {}
_streams_started: set[str] = set()


@asynccontextmanager
async def lifespan(app: FastAPI):
    logger.info("loading renderer model %r ...", engine.model)
    await asyncio.to_thread(engine.load)
    logger.info("renderer ready")
    yield


app = FastAPI(title="ShotBook Renderer", lifespan=lifespan)

# The React app (Vite dev server, reached via an SSH tunnel to this port) is a
# different origin, so it needs CORS to call /generate and open the MJPEG stream.
app.add_middleware(
    CORSMiddleware,
    allow_origins=["http://localhost:5173", "http://127.0.0.1:5173"],
    allow_methods=["*"],
    allow_headers=["*"],
)

# Library "Add story" -> upload .txt/.pdf -> Claude ingestion -> Supabase.
app.include_router(ingest_router)


@app.get("/health")
async def health() -> dict:
    return {"status": "ok", "model": engine.model, "loaded": engine.is_loaded}


# ---------------------------------------------------------------------------
# Shared MJPEG framing
# ---------------------------------------------------------------------------
def _mjpeg(shots: list[dict], out_dir: str, seconds_per_shot: float, control=None):
    """Run the seamless rollout and yield multipart/x-mixed-replace JPEG frames."""
    # Open-ended steering ceiling (latent frames): once the user takes over, the
    # rollout may run up to this long regardless of the planned length.
    max_session_frames = int(round(settings.max_session_seconds * 4))  # LATENT_FPS=4
    with _stream_lock:  # one render at a time on the single GPU
        for frame in engine.stream_plan(shots, out_dir, seconds_per_shot,
                                         control=control, max_session_frames=max_session_frames,
                                         buffer_seconds=settings.realtime_buffer_seconds,
                                         steer_window=settings.realtime_steer_window,
                                         steer_ramp=settings.realtime_steer_ramp_chunks,
                                         await_plan=True):
            bgr = cv2.cvtColor(frame, cv2.COLOR_RGB2BGR)
            ok, jpg = cv2.imencode(".jpg", bgr, [int(cv2.IMWRITE_JPEG_QUALITY), 85])
            if not ok:
                continue
            buf = jpg.tobytes()
            yield (b"--frame\r\nContent-Type: image/jpeg\r\nContent-Length: "
                   + str(len(buf)).encode() + b"\r\n\r\n" + buf + b"\r\n")


# ===========================================================================
# High-level frontend flow: /generate + /jobs/*
# ===========================================================================
async def _plan_in_background(job_id: str, scene) -> None:
    """Phase 7: compose the refined Claude plan WHILE the bootstrap already
    renders, then push the refined shots into the running rollout so it morphs
    from the bootstrap into the cinematic plan. On any failure the bootstrap take
    just plays on (graceful) -- we still mark plan_ready so the UI stops waiting."""
    job = _jobs.get(job_id)
    if job is None:
        return
    try:
        plan = await generate_video_plan(scene)
        job["plan"] = plan.model_dump()
        job["refined_scene"] = scene.model_copy(update={"video": plan}).model_dump()
        job["steer_context"] = plan.shots[-1].prompt if plan.shots else job.get("steer_context", "")
        ctl = job.get("control")
        if ctl is not None:
            ctl.push_plan(job["plan"]["shots"])  # render thread morphs into these
    except Exception as exc:  # noqa: BLE001 - never fail the render on a planning error
        logger.warning("background planning failed for job %s: %s -- keeping bootstrap take", job_id, exc)
    finally:
        if job_id in _jobs:
            _jobs[job_id]["plan_ready"] = True


@app.post("/generate")
async def generate(req: GenerateRequest) -> dict:
    """Start a render IMMEDIATELY from a deterministic bootstrap plan (no LLM) and
    return a job_id right away; the refined Claude shot plan is composed in the
    background and morphed into the already-running rollout (Phase 7). First frame
    lands in ~1-2s instead of waiting on planning."""
    if not req.contexts:
        raise HTTPException(status_code=400, detail="contexts must not be empty")
    if not engine.is_loaded:
        raise HTTPException(status_code=503, detail="model not loaded yet")

    scene = compose_scene(req.contexts)
    boot = bootstrap_plan(scene)
    boot_scene = scene.model_copy(update={"video": boot})

    job_id = str(uuid.uuid4())
    (JOBS_DIR / job_id).mkdir(exist_ok=True)
    _jobs[job_id] = {
        "status": "planned",
        "plan": boot.model_dump(),           # bootstrap now; replaced when Claude returns
        "plan_ready": False,                 # flips true when the refined plan lands
        "refined_scene": None,
        "seconds_per_shot": req.seconds_per_shot or None,
        "video_path": None,
        "error": None,
        # Running description of the current frame, seeded from the bootstrap shot.
        "steer_context": boot.shots[-1].prompt if boot.shots else "",
        "style": boot.world.look,
        # live-control channel for /jobs/{id}/{steer,takeover,pause,resume,finish}
        "control": RenderControl(max_steers=settings.max_steers_per_session),
    }
    # Refine the plan concurrently; the render (opened via /stream) starts on the
    # bootstrap and morphs into this when it's pushed.
    asyncio.create_task(_plan_in_background(job_id, scene))
    return {"job_id": job_id, "scene": boot_scene.model_dump()}


@app.get("/jobs/{job_id}")
def get_job(job_id: str) -> dict:
    job = _jobs.get(job_id)
    if job is None:
        raise HTTPException(status_code=404, detail="job not found")
    control = job.get("control")
    # phase: running | buffering | takeover  (drives the real-time UI controls)
    phase, buffer_remaining, steers_remaining = (
        control.phase_info() if control is not None else (None, None, 0))
    running = job["status"] == "running"
    return {
        "job_id": job_id,
        "status": job["status"],
        "stream_url": f"/jobs/{job_id}/stream" if job["status"] in ("planned", "running") else None,
        "video_url": f"/jobs/{job_id}/video" if job["status"] == "done" else None,
        "error": job.get("error"),
        "phase": phase if running else None,
        "buffer_remaining": buffer_remaining if running else None,
        "steers_remaining": steers_remaining if running else None,
        # Phase 7: the refined Claude plan is composed in the background; the UI
        # swaps its "planning shots…" preview for the real breakdown when ready.
        "plan_ready": job.get("plan_ready", True),
        "scene": job.get("refined_scene"),
    }


def _reclaim_gpu(except_job: str) -> None:
    """Starting a new render implicitly ends any other one: signal every OTHER
    running job to finish so it releases the single-GPU stream lock. Without this
    an ABANDONED takeover (the user took over, never hit Finish, then reloaded and
    generated again) would hold the GPU forever and every new render would block
    on its last frame ("stuck on the buffer screen"). The old loop sees the finish
    flag within ~0.4s, composes what it has, and frees the lock."""
    for jid, j in _jobs.items():
        if jid != except_job and j.get("status") == "running":
            ctl = j.get("control")
            if ctl is not None:
                ctl.finish()


@app.get("/jobs/{job_id}/stream")
def job_stream(job_id: str) -> StreamingResponse:
    """Render the planned job live as MJPEG, then save the finished mp4."""
    job = _jobs.get(job_id)
    if job is None:
        raise HTTPException(status_code=404, detail="job not found")
    if job["status"] == "done":
        raise HTTPException(status_code=410, detail="render already complete — use /video")
    if job["status"] == "failed":
        raise HTTPException(status_code=500, detail=job.get("error") or "render failed")
    if not engine.is_loaded:
        raise HTTPException(status_code=503, detail="model not loaded yet")

    # Reclaim the GPU from any stranded prior render before we queue behind its lock.
    _reclaim_gpu(job_id)

    with _stream_lock:  # guard the started-set; the render lock is taken inside _mjpeg
        if job_id in _streams_started:
            raise HTTPException(status_code=409, detail="stream already in progress")
        _streams_started.add(job_id)

    job_dir = JOBS_DIR / job_id
    seconds = job["seconds_per_shot"] or _planning_settings_seconds()
    shots = job["plan"]["shots"]
    control = job.get("control")
    _jobs[job_id]["status"] = "running"

    def frames():
        try:
            yield from _mjpeg(shots, str(job_dir), seconds, control=control)
            _save_final_mp4(job_id, job_dir)
        except Exception as exc:  # noqa: BLE001 - surface render failures into the job
            _jobs[job_id]["status"] = "failed"
            _jobs[job_id]["error"] = f"render failed: {exc}"[-3000:]
            logger.exception("job %s render failed", job_id)
        finally:
            _streams_started.discard(job_id)

    return StreamingResponse(
        frames(),
        media_type="multipart/x-mixed-replace; boundary=frame",
        headers={"Cache-Control": "no-cache, no-store", "X-Accel-Buffering": "no"},
    )


@app.get("/jobs/{job_id}/video")
def job_video(job_id: str) -> FileResponse:
    job = _jobs.get(job_id)
    if job is None or job["status"] != "done" or not job.get("video_path"):
        raise HTTPException(status_code=404, detail="video not ready")
    return FileResponse(job["video_path"], media_type="video/mp4")


class SteerRequest(BaseModel):
    prompt: str = ""


def _job_control(job_id: str) -> RenderControl:
    job = _jobs.get(job_id)
    if job is None:
        raise HTTPException(status_code=404, detail="job not found")
    control = job.get("control")
    if control is None:
        raise HTTPException(status_code=409, detail="job has no live-control channel")
    return control


@app.post("/jobs/{job_id}/takeover")
def takeover_job(job_id: str) -> dict:
    """Enter takeover mode: the render hands control to the user. Generation
    holds on the last frame until they Steer; each steer renders that scene then
    holds again (no countdown, no auto-finish)."""
    _job_control(job_id).request_takeover()
    return {"ok": True, "phase": "takeover"}


@app.post("/jobs/{job_id}/steer")
async def steer_job(job_id: str, req: SteerRequest) -> dict:
    """In takeover mode, QUEUE a steer -- it edits the current frame then holds.
    Capped at max_steers_per_session; returns whether accepted + how many remain.

    The change is merged (via Claude) with the CURRENT frame's description so the
    engine morphs the same frame (hood -> cap on the same man) instead of cutting
    to a new character. The running description is updated so the next steer builds
    off this one."""
    job = _jobs.get(job_id)
    if job is None:
        raise HTTPException(status_code=404, detail="job not found")
    control = job.get("control")
    if control is None:
        raise HTTPException(status_code=409, detail="job has no live-control channel")
    prompt = await compose_steer_prompt(job.get("steer_context", ""), req.prompt, style=job.get("style", ""))
    accepted, remaining = control.enqueue_steer(prompt)
    if accepted:
        job["steer_context"] = prompt  # build the next change off this frame
    return {"ok": True, "accepted": accepted, "steers_remaining": remaining}


@app.post("/jobs/{job_id}/pause")
def pause_job(job_id: str) -> dict:
    """Pause the post-plan COUNTDOWN so the user has time to decide (buffering
    phase only). No effect once generating/in takeover."""
    _job_control(job_id).pause_countdown()
    return {"ok": True, "paused": True}


@app.post("/jobs/{job_id}/resume")
def resume_job(job_id: str) -> dict:
    """Resume the paused countdown."""
    _job_control(job_id).resume_countdown()
    return {"ok": True, "paused": False}


@app.post("/jobs/{job_id}/finish")
def finish_job(job_id: str) -> dict:
    """Compose & save now: the rollout stops at the next chunk and the mp4
    (everything generated so far) is saved. This is Skip (during the countdown)
    and Finish (during takeover)."""
    _job_control(job_id).finish()
    return {"ok": True, "finishing": True}


def _save_final_mp4(job_id: str, job_dir: Path) -> None:
    final = job_dir / "final_story.mp4"
    if final.exists():
        saved = OUTPUT_DIR / f"video_{datetime.now().strftime('%Y%m%d_%H%M%S')}.mp4"
        shutil.copy(final, saved)
        _jobs[job_id]["status"] = "done"
        _jobs[job_id]["video_path"] = str(saved)
        logger.info("job %s done -> %s", job_id, saved)
    else:
        _jobs[job_id]["status"] = "failed"
        _jobs[job_id]["error"] = "render finished but final_story.mp4 was not found"


def _planning_settings_seconds() -> float:
    return settings.render_seconds_per_shot


# ===========================================================================
# Low-level render API (POST a raw shot list) — kept for testing
# ===========================================================================
@app.post("/render", response_model=RenderResponse)
async def render(req: RenderRequest) -> RenderResponse:
    if not req.shots:
        raise HTTPException(status_code=422, detail="shots must not be empty")
    if not engine.is_loaded:
        raise HTTPException(status_code=503, detail="model not loaded yet")

    shots = [s.model_dump() for s in req.shots]
    t0 = time.time()
    async with _render_lock:
        try:
            final, total_frames = await asyncio.to_thread(
                engine.render_plan, shots, req.out_dir, req.seconds_per_shot
            )
        except Exception as exc:
            logger.exception("render failed")
            raise HTTPException(status_code=500, detail=f"render failed: {exc}") from exc

    return RenderResponse(
        video_path=str(final), shot_count=len(shots),
        total_frames=total_frames, seconds=round(time.time() - t0, 1),
    )


@app.post("/render/stream")
def render_stream(req: RenderRequest) -> StreamingResponse:
    if not req.shots:
        raise HTTPException(status_code=422, detail="shots must not be empty")
    if not engine.is_loaded:
        raise HTTPException(status_code=503, detail="model not loaded yet")
    shots = [s.model_dump() for s in req.shots]
    return StreamingResponse(
        _mjpeg(shots, req.out_dir, req.seconds_per_shot),
        media_type="multipart/x-mixed-replace; boundary=frame",
    )


if __name__ == "__main__":
    import uvicorn

    uvicorn.run("renderer.main:app", host="0.0.0.0", port=8004, reload=False)
