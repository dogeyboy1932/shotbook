"""Video generation job runner.

POST /api/generate-video  -- compose the scene + launch generate_video.py in a
                             background thread; returns immediately with a job_id.
GET  /api/video-jobs/{job_id} -- poll status: pending | running | done | failed
GET  /api/video-jobs/{job_id}/video -- stream the finished mp4
"""
from __future__ import annotations

import json
import shutil
import subprocess
import sys
import threading
import uuid
from datetime import datetime
from pathlib import Path

from fastapi import APIRouter, Depends, HTTPException
from fastapi.responses import FileResponse
from sqlalchemy.ext.asyncio import AsyncSession

from app.context_compiler import compile_contexts
from app.db import get_db_session
from app.scene_composer import compose_scene
from app.schemas import ComposeSceneRequest
from app.video_prompting import VideoPlanningError, generate_video_plan

router = APIRouter(prefix="/api", tags=["video"])

PROJECT_ROOT = Path(__file__).parent.parent.parent
JOBS_DIR = PROJECT_ROOT / "video_jobs"
JOBS_DIR.mkdir(exist_ok=True)

GENERATE_SCRIPT = PROJECT_ROOT / "generate_video.py"

# Finished videos are copied here, named by timestamp, so they can be grabbed
# off disk directly -- no need for the UI to stream them back.
OUTPUT_DIR = PROJECT_ROOT / "generated_videos"
OUTPUT_DIR.mkdir(exist_ok=True)

_jobs: dict[str, dict] = {}


def _run(job_id: str, plan: dict, job_dir: Path) -> None:
    _jobs[job_id]["status"] = "running"
    plan_path = job_dir / "plan.json"
    plan_path.write_text(json.dumps(plan))

    result = subprocess.run(
        # sys.executable, not bare "python" -- guarantees the subprocess runs
        # in the same venv as the API server (which has torch/diffusers/imageio
        # installed). Bare "python" resolves to system python, which doesn't.
        [sys.executable, str(GENERATE_SCRIPT), str(plan_path)],
        cwd=str(job_dir),
        capture_output=True,
        text=True,
    )

    if result.returncode != 0:
        _jobs[job_id]["status"] = "failed"
        _jobs[job_id]["error"] = result.stderr[-3000:]
        return

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
        _jobs[job_id]["error"] = "generate_video.py exited 0 but final_story.mp4 was not produced"


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
    threading.Thread(target=_run, args=(job_id, plan_dict, job_dir), daemon=True).start()

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
