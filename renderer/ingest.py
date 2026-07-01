"""Story-ingestion endpoints on the renderer (the single VM backend).

Lets the Library "Add" button upload a .txt or .pdf, parse it, and kick off the
Claude ingestion pipeline (ingestion/orchestrator.py) straight into Supabase --
with a progress bar + ETA polled from the frontend.

Ingestion is GPU-free (Claude + Postgres), so it runs as an asyncio task in the
renderer process without touching the model.
"""
from __future__ import annotations

import asyncio
import io
import logging
import os
import sys
import time
import uuid

from fastapi import APIRouter, File, Form, HTTPException, UploadFile

# Ensure the repo root is importable so `ingestion` + `app` resolve when the
# service is launched via the uvicorn console script (which, unlike `python -m`,
# doesn't put the working dir on sys.path).
_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if _ROOT not in sys.path:
    sys.path.insert(0, _ROOT)

logger = logging.getLogger("renderer.ingest")
router = APIRouter()

# ingest_job_id -> {status, stage, completed, total, progress, started_at, book_id, error}
_ingest_jobs: dict[str, dict] = {}


def _parse_upload(filename: str, data: bytes) -> str:
    """Extract plain text from a .txt or .pdf upload."""
    name = (filename or "").lower()
    if name.endswith(".pdf"):
        from pypdf import PdfReader

        reader = PdfReader(io.BytesIO(data))
        return "\n\n".join((page.extract_text() or "") for page in reader.pages)
    # Everything else is treated as UTF-8 text (.txt and friends).
    return data.decode("utf-8", errors="replace")


async def _run_ingest(job_id: str, title: str, author: str | None, source_uri: str, text: str) -> None:
    job = _ingest_jobs[job_id]

    def cb(stage: str, completed: int, total: int) -> None:
        job.update(
            stage=stage,
            completed=completed,
            total=total,
            progress=round(completed / total, 4) if total else 0.0,
        )

    try:
        # Deferred import kept inside the try so an import failure surfaces as a
        # failed job (with error) instead of an unretrieved task exception that
        # leaves the job silently stuck at "queued".
        from ingestion.orchestrator import ingest_book

        job["status"] = "running"
        book_id = await ingest_book(
            title=title, author=author, source_uri=source_uri, raw_book_text=text, progress_cb=cb
        )
        job.update(status="done", book_id=book_id, progress=1.0, stage="Done")
        logger.info("ingest %s done -> book_id=%s", job_id, book_id)
    except Exception as exc:  # noqa: BLE001 - surface into the job
        job.update(status="failed", error=str(exc)[:500])
        logger.exception("ingest %s failed", job_id)


@router.post("/ingest")
async def ingest(
    file: UploadFile = File(...),
    title: str = Form(...),
    author: str = Form(""),
) -> dict:
    """Upload a .txt/.pdf, parse it, and start ingestion. Returns an ingest_job_id."""
    data = await file.read()
    if not data:
        raise HTTPException(status_code=400, detail="empty file")
    try:
        text = _parse_upload(file.filename or "", data)
    except Exception as exc:  # noqa: BLE001
        raise HTTPException(status_code=400, detail=f"could not parse file: {exc}") from exc
    if not text.strip():
        raise HTTPException(status_code=422, detail="no text could be extracted from the file")

    job_id = str(uuid.uuid4())
    _ingest_jobs[job_id] = {
        "status": "queued", "stage": "Queued", "completed": 0, "total": 0,
        "progress": 0.0, "started_at": time.time(), "book_id": None, "error": None,
    }
    asyncio.create_task(_run_ingest(job_id, title, author or None, file.filename or title, text))
    return {"ingest_job_id": job_id}


@router.get("/ingest/{job_id}")
def ingest_status(job_id: str) -> dict:
    job = _ingest_jobs.get(job_id)
    if job is None:
        raise HTTPException(status_code=404, detail="ingest job not found")
    elapsed = time.time() - job["started_at"]
    eta = None
    if job["status"] == "running" and job["completed"] > 0 and job["total"] > 0:
        eta = round((elapsed / job["completed"]) * (job["total"] - job["completed"]))
    return {
        "ingest_job_id": job_id,
        "status": job["status"],
        "stage": job["stage"],
        "completed": job["completed"],
        "total": job["total"],
        "progress": job["progress"],
        "eta_seconds": eta,
        "book_id": job["book_id"],
        "error": job["error"],
    }
