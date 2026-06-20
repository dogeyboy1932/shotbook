import json
from contextlib import asynccontextmanager

import httpx
from fastapi import FastAPI, HTTPException, WebSocket, WebSocketDisconnect
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import StreamingResponse

from .multi_mixer import mix_multi_dialogue
from .orchestrator import dispatch, dispatch_from_prompt
from .pipe_mixer import mix_audio
from .schema import AudioPromptRequest, ScriptBlock

_http_client: httpx.AsyncClient | None = None


@asynccontextmanager
async def lifespan(app: FastAPI):
    global _http_client
    _http_client = httpx.AsyncClient(
        timeout=httpx.Timeout(connect=5.0, read=120.0, write=30.0, pool=5.0)
    )
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


@app.post("/audio_prompt")
async def audio_prompt_endpoint(request: AudioPromptRequest):
    """Accept an audio_prompt text block and return mixed audio.

    The audio_prompt format describes ambient sound effects and multi-character
    dialogue with voice characteristics. The backend generates TTS for each
    dialogue line, generates SFX for ambient sounds, sequences them with proper
    timing, and returns AAC audio in an MP4 container ready for video muxing.

    Returns a streaming response with ``audio/mpeg`` MIME type (MP3).
    """
    # Pre-flight: check downstream services are reachable
    try:
        from .orchestrator import SFX_SERVICE_URL, TTS_SERVICE_URL

        try:
            r = await _http_client.get(f"{TTS_SERVICE_URL}/health", timeout=3.0)
            r.raise_for_status()
        except Exception:
            raise HTTPException(
                status_code=503,
                detail=f"TTS service unreachable at {TTS_SERVICE_URL}. "
                "Start it with: make start (or ensure port 8001 is up)",
            )
        try:
            r = await _http_client.get(f"{SFX_SERVICE_URL}/health", timeout=3.0)
            r.raise_for_status()
        except Exception:
            raise HTTPException(
                status_code=503,
                detail=f"SFX service unreachable at {SFX_SERVICE_URL}. "
                "Start it with: make start (or ensure port 8002 is up)",
            )
    except HTTPException:
        raise

    (
        dialogue_pcms,
        sfx_pcm,
        total_duration_ms,
        dialogue_texts,
    ) = await dispatch_from_prompt(
        _http_client,
        request.audio_prompt,
        request.book_id,
        gap_ms=request.gap_between_lines_ms,
    )

    async def generate():
        async for chunk in mix_multi_dialogue(
            dialogue_pcms,
            sfx_pcm,
            total_duration_ms,
            gap_ms=request.gap_between_lines_ms,
            speed=request.speed,
            dialogue_texts=dialogue_texts,
        ):
            yield chunk

    return StreamingResponse(generate(), media_type="audio/mpeg")


if __name__ == "__main__":
    import uvicorn

    uvicorn.run("services.mixer.main:app", host="0.0.0.0", port=8003, reload=False)
