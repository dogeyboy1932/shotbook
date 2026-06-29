"""Renderer microservice (FastAPI).

Loads the streaming video model ONCE at startup (warm) and serves render
requests. The ShotBook backend (app/routers/video_jobs.py) POSTs a video plan
here instead of subprocess-spawning a cold-loading generate_video.py.

Runs in its OWN venv (.venv-renderer) -- the engine needs torch>=2.4 /
diffusers 0.31 / flash-attn, which conflict with the backend's pinned torch 2.1.

    CUDA_VISIBLE_DEVICES=0 .venv-renderer/bin/uvicorn services.renderer.main:app \
        --host 0.0.0.0 --port 8004
"""
from __future__ import annotations

import asyncio
import logging
import os
import threading
import time
from contextlib import asynccontextmanager

import cv2
from fastapi import FastAPI, HTTPException
from fastapi.responses import StreamingResponse

from services.renderer.renderer import RenderEngine
from services.renderer.schema import RenderRequest, RenderResponse

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger("renderer.main")

engine = RenderEngine(model=os.getenv("RENDERER_MODEL", "chunkwise"))

# Single GPU, single model instance -> serialize renders so two requests never
# stomp on the same KV/VAE caches or contend for VRAM.
_render_lock = asyncio.Lock()
# The live MJPEG endpoint runs in a worker thread (sync generator), so it needs
# a plain threading lock rather than the asyncio one above.
_stream_lock = threading.Lock()


@asynccontextmanager
async def lifespan(app: FastAPI):
    logger.info("loading renderer model %r ...", engine.model)
    await asyncio.to_thread(engine.load)   # warm load off the event loop
    logger.info("renderer ready")
    yield


app = FastAPI(title="ShotBook Renderer", lifespan=lifespan)


@app.get("/health")
async def health() -> dict:
    return {"status": "ok", "model": engine.model, "loaded": engine.is_loaded}


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
        except Exception as exc:  # surface render failures as 500 with the message
            logger.exception("render failed")
            raise HTTPException(status_code=500, detail=f"render failed: {exc}") from exc

    return RenderResponse(
        video_path=str(final),
        shot_count=len(shots),
        total_frames=total_frames,
        seconds=round(time.time() - t0, 1),
    )


@app.post("/render/stream")
def render_stream(req: RenderRequest) -> StreamingResponse:
    """Live MJPEG: stream each frame as the model produces it (multipart/
    x-mixed-replace), so the browser can display frames as they generate. The
    finished mp4 is still written to req.out_dir by stream_plan.

    Sync endpoint on purpose -- FastAPI runs it in a worker thread, and the
    StreamingResponse drives the sync generator there, so the blocking
    GPU/encode loop never touches the event loop.
    """
    if not req.shots:
        raise HTTPException(status_code=422, detail="shots must not be empty")
    if not engine.is_loaded:
        raise HTTPException(status_code=503, detail="model not loaded yet")
    shots = [s.model_dump() for s in req.shots]

    def frames():
        with _stream_lock:  # one render at a time on the single GPU
            for frame in engine.stream_plan(shots, req.out_dir, req.seconds_per_shot):
                bgr = cv2.cvtColor(frame, cv2.COLOR_RGB2BGR)
                ok, jpg = cv2.imencode(".jpg", bgr, [int(cv2.IMWRITE_JPEG_QUALITY), 85])
                if not ok:
                    continue
                buf = jpg.tobytes()
                yield (b"--frame\r\nContent-Type: image/jpeg\r\nContent-Length: "
                       + str(len(buf)).encode() + b"\r\n\r\n" + buf + b"\r\n")

    return StreamingResponse(
        frames(), media_type="multipart/x-mixed-replace; boundary=frame"
    )


if __name__ == "__main__":
    import uvicorn

    uvicorn.run("services.renderer.main:app", host="0.0.0.0", port=8004, reload=False)
