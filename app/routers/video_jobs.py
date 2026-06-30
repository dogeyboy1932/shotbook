"""Video generation job runner.

POST /api/generate-video       -- compose the scene + plan shots; returns job_id
                                  (render starts when the browser opens the stream).
GET  /api/video-jobs/{job_id}  -- poll status: planned | running | done | failed
GET  /api/video-jobs/{job_id}/stream -- live MJPEG relay from the renderer
GET  /api/video-jobs/{job_id}/video    -- stream the finished mp4

Rendering is delegated to the warm renderer microservice (services/renderer,
default http://localhost:8004). The browser connects to /stream immediately
after planning so frames appear in real time via multipart/x-mixed-replace.
"""
from __future__ import annotations

import shutil
import threading
import uuid
from datetime import datetime
from pathlib import Path

import httpx
from fastapi import APIRouter, Depends, HTTPException
from fastapi.responses import FileResponse, StreamingResponse
from sqlalchemy.ext.asyncio import AsyncSession

from app.config import settings
from app.context_compiler import compile_contexts
from app.db import get_db_session
from app.scene_composer import compose_scene
from app.schemas import ComposeSceneRequest
from app.video_prompting import VideoPlanningError, generate_video_plan

router = APIRouter(prefix="/api", tags=["video"])

PROJECT_ROOT = Path(__file__).parent.parent.parent
JOBS_DIR = PROJECT_ROOT / "video_jobs"
JOBS_DIR.mkdir(exist_ok=True)

OUTPUT_DIR = PROJECT_ROOT / "generated_videos"
OUTPUT_DIR.mkdir(exist_ok=True)

_RENDER_TIMEOUT_S = 1800.0

_jobs: dict[str, dict] = {}
# Prevent two concurrent stream connections from starting duplicate GPU renders.
_stream_lock = threading.Lock()
_streams_started: set[str] = set()


def _save_final_mp4(job_id: str, job_dir: Path) -> None:
    """Copy final_story.mp4 into generated_videos/ and mark the job done."""
    final = job_dir / "final_story.mp4"
    if final.exists():
        timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
        saved = OUTPUT_DIR / f"video_{timestamp}.mp4"
        shutil.copy(final, saved)
        print(f"[video_jobs] saved generated video to {saved}", flush=True)
        _jobs[job_id]["status"] = "done"
        _jobs[job_id]["video_path"] = str(saved)
    else:
        _jobs[job_id]["status"] = "failed"
        _jobs[job_id]["error"] = "renderer finished but final_story.mp4 was not found"


def _render_payload(plan: dict, job_dir: Path) -> dict:
    return {
        "shots": plan["shots"],
        "out_dir": str(job_dir),
        "seconds_per_shot": settings.render_seconds_per_shot,
        "negative_prompt": plan.get("negative_prompt"),
    }


@router.post("/generate-video")
async def generate_video(
    request: ComposeSceneRequest,
    session: AsyncSession = Depends(get_db_session),
) -> dict:
    if not request.paragraph_ids:
        raise HTTPException(status_code=400, detail="paragraph_ids must not be empty")

    payloads = await compile_contexts(session, request.paragraph_ids)
    if not payloads:
        raise HTTPException(status_code=404, detail="none of the requested paragraph_ids were found")

    scene = compose_scene(payloads)
    try:
        video_plan = await generate_video_plan(scene)
    except VideoPlanningError as exc:
        raise HTTPException(status_code=503, detail=str(exc)) from exc

    scene = scene.model_copy(update={"video": video_plan})

    job_id = str(uuid.uuid4())
    job_dir = JOBS_DIR / job_id
    job_dir.mkdir()

    plan_dict = video_plan.model_dump()
    _jobs[job_id] = {
        "status": "planned",
        "video_path": None,
        "error": None,
        "plan": plan_dict,
    }

    return {"job_id": job_id, "scene": scene.model_dump()}


@router.get("/video-jobs/{job_id}")
def get_job(job_id: str) -> dict:
    job = _jobs.get(job_id)
    if job is None:
        raise HTTPException(status_code=404, detail="job not found")
    return {
        "job_id": job_id,
        "status": job["status"],
        "video_url": f"/api/video-jobs/{job_id}/video" if job["status"] == "done" else None,
        "stream_url": f"/api/video-jobs/{job_id}/stream" if job["status"] in ("planned", "running") else None,
        "error": job.get("error"),
    }


@router.get("/video-jobs/{job_id}/stream")
def stream_video(job_id: str) -> StreamingResponse:
    """Relay the renderer's live MJPEG stream to the browser.

    Opens a single POST to the renderer's /render/stream and pipes each
    multipart chunk through unchanged so <img src="..."> can display frames
    as they are produced.
    """
    job = _jobs.get(job_id)
    if job is None:
        raise HTTPException(status_code=404, detail="job not found")
    if job["status"] == "done":
        raise HTTPException(status_code=410, detail="render already complete — use /video")
    if job["status"] == "failed":
        raise HTTPException(status_code=500, detail=job.get("error") or "render failed")
    if job["status"] == "running":
        raise HTTPException(status_code=409, detail="stream already in progress")

    plan = job.get("plan")
    if not plan:
        raise HTTPException(status_code=500, detail="job has no render plan")

    with _stream_lock:
        if job_id in _streams_started:
            raise HTTPException(status_code=409, detail="stream already in progress")
        _streams_started.add(job_id)

    job_dir = JOBS_DIR / job_id
    _jobs[job_id]["status"] = "running"
    payload = _render_payload(plan, job_dir)

    def relay():
        try:
            with httpx.stream(
                "POST",
                f"{settings.renderer_url}/render/stream",
                json=payload,
                timeout=_RENDER_TIMEOUT_S,
            ) as response:
                response.raise_for_status()
                for chunk in response.iter_bytes():
                    yield chunk
            _save_final_mp4(job_id, job_dir)
        except Exception as exc:
            _jobs[job_id]["status"] = "failed"
            _jobs[job_id]["error"] = f"stream relay failed: {exc}"[-3000:]
        finally:
            _streams_started.discard(job_id)

    return StreamingResponse(
        relay(),
        media_type="multipart/x-mixed-replace; boundary=frame",
        headers={"Cache-Control": "no-cache, no-store", "X-Accel-Buffering": "no"},
    )


@router.get("/video-jobs/{job_id}/video")
def get_video(job_id: str) -> FileResponse:
    job = _jobs.get(job_id)
    if job is None or job["status"] != "done":
        raise HTTPException(status_code=404, detail="video not ready")
    return FileResponse(job["video_path"], media_type="video/mp4")
