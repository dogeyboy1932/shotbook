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

    CUDA_VISIBLE_DEVICES=0 .venv-renderer/bin/uvicorn services.renderer.main:app \
        --host 0.0.0.0 --port 8004
"""
from __future__ import annotations

import asyncio
import logging
import os
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

from services.renderer.planning import VideoPlanningError, compose_scene, generate_video_plan
from services.renderer.renderer import RenderEngine
from services.renderer.schema import RenderRequest, RenderResponse
from services.renderer.schemas import GenerateRequest

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger("renderer.main")

engine = RenderEngine(model=os.getenv("RENDERER_MODEL", "chunkwise"))

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


@app.get("/health")
async def health() -> dict:
    return {"status": "ok", "model": engine.model, "loaded": engine.is_loaded}


# ---------------------------------------------------------------------------
# Shared MJPEG framing
# ---------------------------------------------------------------------------
def _mjpeg(shots: list[dict], out_dir: str, seconds_per_shot: float):
    """Run the seamless rollout and yield multipart/x-mixed-replace JPEG frames."""
    with _stream_lock:  # one render at a time on the single GPU
        for frame in engine.stream_plan(shots, out_dir, seconds_per_shot):
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
@app.post("/generate")
async def generate(req: GenerateRequest) -> dict:
    """Plan shots for the highlighted span's resolved contexts (from Supabase),
    store the plan, and return a job_id. The render begins when the browser
    opens /jobs/{id}/stream."""
    if not req.contexts:
        raise HTTPException(status_code=400, detail="contexts must not be empty")
    if not engine.is_loaded:
        raise HTTPException(status_code=503, detail="model not loaded yet")

    scene = compose_scene(req.contexts)
    try:
        plan = await generate_video_plan(scene)
    except VideoPlanningError as exc:
        raise HTTPException(status_code=503, detail=str(exc)) from exc
    scene = scene.model_copy(update={"video": plan})

    job_id = str(uuid.uuid4())
    (JOBS_DIR / job_id).mkdir(exist_ok=True)
    _jobs[job_id] = {
        "status": "planned",
        "plan": plan.model_dump(),
        "seconds_per_shot": req.seconds_per_shot or None,
        "video_path": None,
        "error": None,
    }
    return {"job_id": job_id, "scene": scene.model_dump()}


@app.get("/jobs/{job_id}")
def get_job(job_id: str) -> dict:
    job = _jobs.get(job_id)
    if job is None:
        raise HTTPException(status_code=404, detail="job not found")
    return {
        "job_id": job_id,
        "status": job["status"],
        "stream_url": f"/jobs/{job_id}/stream" if job["status"] in ("planned", "running") else None,
        "video_url": f"/jobs/{job_id}/video" if job["status"] == "done" else None,
        "error": job.get("error"),
    }


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

    with _stream_lock:  # guard the started-set; the render lock is taken inside _mjpeg
        if job_id in _streams_started:
            raise HTTPException(status_code=409, detail="stream already in progress")
        _streams_started.add(job_id)

    job_dir = JOBS_DIR / job_id
    seconds = job["seconds_per_shot"] or _planning_settings_seconds()
    shots = job["plan"]["shots"]
    _jobs[job_id]["status"] = "running"

    def frames():
        try:
            yield from _mjpeg(shots, str(job_dir), seconds)
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
    from services.renderer.config import settings
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

    uvicorn.run("services.renderer.main:app", host="0.0.0.0", port=8004, reload=False)
