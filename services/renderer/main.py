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
import time
from contextlib import asynccontextmanager

from fastapi import FastAPI, HTTPException

from services.renderer.renderer import RenderEngine
from services.renderer.schema import RenderRequest, RenderResponse

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger("renderer.main")

engine = RenderEngine(model=os.getenv("RENDERER_MODEL", "chunkwise"))

# Single GPU, single model instance -> serialize renders so two requests never
# stomp on the same KV/VAE caches or contend for VRAM.
_render_lock = asyncio.Lock()


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


if __name__ == "__main__":
    import uvicorn

    uvicorn.run("services.renderer.main:app", host="0.0.0.0", port=8004, reload=False)
