"""Background job orchestrator for parallel video + audio generation."""
from __future__ import annotations

import asyncio, json, os, subprocess, tempfile, time, uuid
from dataclasses import dataclass, field
from pathlib import Path

_jobs: dict[str, "ComposeJob"] = {}
JOB_TTL_SECONDS = 3600

@dataclass
class ComposeJob:
    job_id: str
    status: str = "queued"
    video_prompt: str = ""
    audio_prompt: str = ""
    negative_prompt: str = ""
    progress: str = ""
    result_path: str | None = None
    error: str | None = None
    created_at: float = field(default_factory=time.time)

def create_job(vp, np, ap, bid, spd, gap):
    jid = uuid.uuid4().hex[:12]
    job = ComposeJob(job_id=jid, video_prompt=vp, audio_prompt=ap, negative_prompt=np)
    _jobs[jid] = job
    return job

def get_job(jid):
    return _jobs.get(jid)

async def run_compose_job(job, http_client, book_id, speed, gap_ms, output_dir="/tmp"):
    from .orchestrator import dispatch_from_prompt
    from .multi_mixer import mix_multi_dialogue
    try:
        job.status = "generating_audio"
        job.progress = "Synthesizing speech and sound effects..."
        fd, apath = tempfile.mkstemp(suffix=".mp3", dir=output_dir)
        os.close(fd)
        pcms, sfx, tms, txts, _ = await dispatch_from_prompt(http_client, job.audio_prompt, book_id, gap_ms=gap_ms)
        with open(apath, "wb") as f:
            async for c in mix_multi_dialogue(pcms, sfx, tms, gap_ms=gap_ms, speed=speed, dialogue_texts=txts):
                f.write(c)
        job.progress = "Audio done. Waiting for video (30-40min)..."
        job.status = "generating_video"
        plan = {"shots": [{"shot_id": "scene", "prompt": job.video_prompt}], "negative_prompt": job.negative_prompt}
        fd2, ppath = tempfile.mkstemp(suffix=".json", dir=output_dir)
        os.close(fd2)
        with open(ppath, "w") as f: json.dump(plan, f)
        root = Path(__file__).resolve().parent.parent.parent
        gs = root / "generate_video.py"
        env = os.environ.copy(); env["CUDA_VISIBLE_DEVICES"] = "3"
        proc = await asyncio.create_subprocess_exec(".venv/bin/python", str(gs), ppath, stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.PIPE, cwd=str(root), env=env)
        so, se = await proc.communicate()
        try: os.unlink(ppath)
        except OSError: pass
        if proc.returncode != 0: raise RuntimeError(f"Video failed: {se.decode()[:300]}")
        vpath = str(root / "wan22_scene.mp4")
        if not os.path.exists(vpath): raise FileNotFoundError(f"Video not found: {vpath}")
        job.status = "muxing"
        job.progress = "Muxing audio and video..."
        fd3, rpath = tempfile.mkstemp(suffix=".mp4", dir=output_dir)
        os.close(fd3)
        mux = await asyncio.create_subprocess_exec("ffmpeg", "-y", "-nostdin", "-i", vpath, "-i", apath, "-c:v", "copy", "-c:a", "aac", "-map", "0:v", "-map", "1:a", "-shortest", "-f", "mp4", "-loglevel", "error", rpath, stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.PIPE)
        await mux.communicate()
        if mux.returncode != 0: raise RuntimeError("Mux failed")
        job.result_path = rpath
        job.status = "done"
        job.progress = "Complete!"
        try: os.unlink(apath)
        except OSError: pass
    except Exception as e:
        job.status = "failed"
        job.error = str(e)
        job.progress = f"Failed: {e}"
