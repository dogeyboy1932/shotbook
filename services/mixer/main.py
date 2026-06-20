import json
from contextlib import asynccontextmanager

import httpx
from fastapi import FastAPI, WebSocket, WebSocketDisconnect
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import StreamingResponse

from .orchestrator import dispatch
from .pipe_mixer import mix_audio
from .schema import ScriptBlock

_http_client: httpx.AsyncClient | None = None


@asynccontextmanager
async def lifespan(app: FastAPI):
    global _http_client
    _http_client = httpx.AsyncClient(timeout=httpx.Timeout(connect=5.0, read=120.0, write=30.0, pool=5.0))
    yield
    await _http_client.aclose()


app = FastAPI(title="Audio Mixer", lifespan=lifespan)

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_methods=["*"],
    allow_headers=["*"],
)


@app.get("/health")
async def health():
    return {"status": "ok"}


@app.websocket("/ws/audio")
async def ws_audio(websocket: WebSocket):
    await websocket.accept()
    try:
        raw = await websocket.receive_text()
        block = ScriptBlock.model_validate(json.loads(raw))

        vocal_stream, sfx_cues = await dispatch(_http_client, block)

        async for mp3_chunk in mix_audio(vocal_stream, sfx_cues):
            await websocket.send_bytes(mp3_chunk)

        await websocket.send_text(json.dumps({"done": True}))
    except WebSocketDisconnect:
        pass
    except Exception as exc:
        try:
            await websocket.send_text(json.dumps({"error": str(exc)}))
        except Exception:
            pass
        raise


@app.post("/mix")
async def mix_rest(block: ScriptBlock):
    vocal_stream, sfx_cues = await dispatch(_http_client, block)

    async def generate():
        async for chunk in mix_audio(vocal_stream, sfx_cues):
            yield chunk

    return StreamingResponse(generate(), media_type="audio/mpeg")


if __name__ == "__main__":
    import uvicorn
    uvicorn.run("services.mixer.main:app", host="0.0.0.0", port=8003, reload=False)
