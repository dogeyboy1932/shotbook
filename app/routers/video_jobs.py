"""Video generation job runner.

POST /api/generate-video  -- compose the scene + dispatch the planned shots to the
                             renderer service in a background thread; returns a job_id.
GET  /api/video-jobs/{job_id} -- poll status: pending | running | done | failed
GET  /api/video-jobs/{job_id}/video -- stream the finished mp4

Rendering itself is delegated to the warm renderer microservice
(services/renderer, default http://localhost:8004) -- a persistent process that
keeps the Wan2.1-1.3B streaming model in GPU memory, so each job pays no
model-load cost. This replaced the old approach of subprocess-spawning a
cold-loading generate_video.py per job.
"""
from __future__ import annotations

import shutil
import threading
import uuid
from datetime import datetime
from pathlib import Path

import httpx
from fastapi import APIRouter, Depends, HTTPException
from fastapi.responses import FileResponse
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

# Finished videos are copied here, named by timestamp, so they can be grabbed
# off disk directly -- no need for the UI to stream them back.
OUTPUT_DIR = PROJECT_ROOT / "generated_videos"
OUTPUT_DIR.mkdir(exist_ok=True)

# Generous: a multi-shot scene streams several clips back-to-back on one GPU.
_RENDER_TIMEOUT_S = 1800.0

_jobs: dict[str, dict] = {}


def _run(job_id: str, plan: dict, job_dir: Path, quality: bool = False) -> None:
    """Hand the planned shots to the renderer service and record the result.

    Routes to the cinematic 5B renderer when `quality` is set, otherwise the
    fast 1.3B streaming renderer. The chosen renderer shares this VM's
    filesystem, so it writes final_story.mp4 into `job_dir` directly; we just
    copy it into generated_videos/ on success.
    """
    _jobs[job_id]["status"] = "running"
    renderer_url = settings.quality_renderer_url if quality else settings.renderer_url
    seconds_per_shot = (
        settings.quality_seconds_per_shot if quality else settings.render_seconds_per_shot
    )
    payload = {
        "shots": plan["shots"],
        "out_dir": str(job_dir),
        "seconds_per_shot": seconds_per_shot,
        "negative_prompt": plan.get("negative_prompt"),
    }

    try:
        response = httpx.post(
            f"{renderer_url}/render", json=payload, timeout=_RENDER_TIMEOUT_S
        )
        response.raise_for_status()
        result = response.json()
    except Exception as exc:  # connection refused, timeout, 5xx from renderer, etc.
        _jobs[job_id]["status"] = "failed"
        _jobs[job_id]["error"] = f"renderer call failed: {exc}"[-3000:]
        return

    final = Path(result.get("video_path") or (job_dir / "final_story.mp4"))
    if final.exists():
        timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
        saved = OUTPUT_DIR / f"video_{timestamp}.mp4"
        shutil.copy(final, saved)
        print(f"[video_jobs] saved generated video to {saved}", flush=True)
        _jobs[job_id]["status"] = "done"
        _jobs[job_id]["video_path"] = str(saved)
    else:
        _jobs[job_id]["status"] = "failed"
        _jobs[job_id]["error"] = "renderer returned ok but final_story.mp4 was not found"


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

    _jobs[job_id] = {"status": "pending", "video_path": None, "error": None}

    plan_dict = video_plan.model_dump()
    threading.Thread(
        target=_run, args=(job_id, plan_dict, job_dir, request.quality), daemon=True
    ).start()

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
        "error": job.get("error"),
    }


@router.get("/video-jobs/{job_id}/video")
def get_video(job_id: str) -> FileResponse:
    job = _jobs.get(job_id)
    if job is None or job["status"] != "done":
        raise HTTPException(status_code=404, detail="video not ready")
    return FileResponse(job["video_path"], media_type="video/mp4")
