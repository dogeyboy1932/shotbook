"""Quality (5B) renderer microservice (FastAPI) -- the HD half of the hybrid.

Loads Wan2.2-TI2V-5B ONCE (warm) and serves the same /render contract as the
fast renderer, so app/routers/video_jobs.py just points at a different URL when
a job asks for quality=true.

Runs in its OWN venv (.venv-quality, newer diffusers) on a separate port so it
can sit warm alongside the fast 1.3B renderer on the same GPU.

    CUDA_VISIBLE_DEVICES=0 .venv-quality/bin/uvicorn renderer.quality_main:app \
        --host 0.0.0.0 --port 8005
"""
from __future__ import annotations

import asyncio
import logging
import time
from contextlib import asynccontextmanager

from fastapi import FastAPI, HTTPException

from renderer.quality_engine import QualityEngine
from renderer.schema import RenderRequest, RenderResponse

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger("quality.main")

engine = QualityEngine()

# Single GPU, single model instance -> serialize renders.
_render_lock = asyncio.Lock()


@asynccontextmanager
async def lifespan(app: FastAPI):
    logger.info("loading quality (5B) model ...")
    await asyncio.to_thread(engine.load)
    logger.info("quality renderer ready")
    yield


app = FastAPI(title="ShotBook Quality Renderer", lifespan=lifespan)


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
                engine.render_plan, shots, req.out_dir, req.seconds_per_shot,
                req.negative_prompt, req.steps, req.guidance
            )
        except Exception as exc:
            logger.exception("HD render failed")
            raise HTTPException(status_code=500, detail=f"render failed: {exc}") from exc

    return RenderResponse(
        video_path=str(final),
        shot_count=len(shots),
        total_frames=total_frames,
        seconds=round(time.time() - t0, 1),
    )


if __name__ == "__main__":
    import uvicorn

    uvicorn.run("renderer.quality_main:app", host="0.0.0.0", port=8005, reload=False)
