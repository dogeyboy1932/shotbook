from contextlib import asynccontextmanager

import httpx
from fastapi import FastAPI, HTTPException
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import StreamingResponse

from .fish_audio import synthesize_stream
from .schema import SynthesizeRequest

_http_client: httpx.AsyncClient | None = None


@asynccontextmanager
async def lifespan(app: FastAPI):
    global _http_client
    _http_client = httpx.AsyncClient()
    yield
    await _http_client.aclose()
    _http_client = None


app = FastAPI(lifespan=lifespan)

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_methods=["*"],
    allow_headers=["*"],
)


@app.get("/health")
async def health():
    return {"status": "ok"}


@app.post("/synthesize")
async def synthesize(req: SynthesizeRequest):
    if _http_client is None:
        raise HTTPException(status_code=503, detail="Service not ready")

    return StreamingResponse(
        synthesize_stream(req, _http_client),
        media_type="audio/x-raw",
    )


if __name__ == "__main__":
    import uvicorn

    uvicorn.run("services.tts.main:app", host="0.0.0.0", port=8001)
