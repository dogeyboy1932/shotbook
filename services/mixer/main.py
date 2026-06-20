import asyncio
import json
import os
from contextlib import asynccontextmanager

import httpx
from fastapi import FastAPI, HTTPException, WebSocket, WebSocketDisconnect
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import StreamingResponse

from .multi_mixer import mix_multi_dialogue
from .orchestrator import dispatch, dispatch_from_prompt
from .pipe_mixer import mix_audio
from .schema import (
    AudioPromptRequest,
    ComposeSceneRequest,
    JobStatus,
    MuxRequest,
    ScriptBlock,
)

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
        _speaker_names,
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


@app.post("/mux")
async def mux_endpoint(request: MuxRequest):
    """Combine an existing video file with generated or existing audio."""
    import subprocess
    import tempfile

    if not request.audio_prompt and not request.audio_path:
        raise HTTPException(
            status_code=422,
            detail="Either audio_prompt or audio_path must be provided",
        )
    if not os.path.exists(request.video_path):
        raise HTTPException(
            status_code=422,
            detail=f"Video file not found: {request.video_path}",
        )

    audio_input: str
    cleanup_audio = False

    if request.audio_path:
        if not os.path.exists(request.audio_path):
            raise HTTPException(
                status_code=422,
                detail=f"Audio file not found: {request.audio_path}",
            )
        audio_input = request.audio_path
    else:
        (
            dialogue_pcms,
            sfx_pcm,
            total_duration_ms,
            dialogue_texts,
            _speaker_names,
        ) = await dispatch_from_prompt(
            _http_client,
            request.audio_prompt,  # type: ignore[arg-type]
            request.book_id,
            gap_ms=request.gap_between_lines_ms,
        )
        tmp = tempfile.NamedTemporaryFile(suffix=".mp3", delete=False)
        audio_input = tmp.name
        cleanup_audio = True
        async for chunk in mix_multi_dialogue(
            dialogue_pcms,
            sfx_pcm,
            total_duration_ms,
            gap_ms=request.gap_between_lines_ms,
            speed=request.speed,
            dialogue_texts=dialogue_texts,
        ):
            tmp.write(chunk)
        tmp.close()

    # Mux with sync subprocess to temp file, then stream
    import subprocess as sp

    out_tmp = tempfile.NamedTemporaryFile(suffix=".mp4", delete=False)
    out_path = out_tmp.name
    out_tmp.close()

    result = sp.run(
        [
            "ffmpeg",
            "-y",
            "-nostdin",
            "-i",
            request.video_path,
            "-i",
            audio_input,
            "-c:v",
            "copy",
            "-c:a",
            "aac",
            "-map",
            "0:v",
            "-map",
            "1:a",
            "-shortest",
            "-f",
            "mp4",
            "-loglevel",
            "error",
            out_path,
        ],
        capture_output=True,
        text=True,
        timeout=120,
    )
    if result.returncode != 0:
        if cleanup_audio:
            os.unlink(audio_input)
        os.unlink(out_path)
        raise HTTPException(
            status_code=500,
            detail=f"ffmpeg failed: {result.stderr[:300]}",
        )

    def file_stream():
        with open(out_path, "rb") as f:
            while True:
                chunk = f.read(65536)
                if not chunk:
                    break
                yield chunk
        if cleanup_audio:
            try:
                os.unlink(audio_input)
            except OSError:
                pass
        try:
            os.unlink(out_path)
        except OSError:
            pass

    return StreamingResponse(file_stream(), media_type="video/mp4")


@app.post("/compose-scene", response_model=JobStatus)
async def compose_scene(request: ComposeSceneRequest):
    """Start parallel video + audio generation. Returns job_id for polling."""
    from .composer import create_job, run_compose_job

    # Quick pre-flight
    try:
        from .orchestrator import SFX_SERVICE_URL, TTS_SERVICE_URL

        for name, url in [("TTS", TTS_SERVICE_URL), ("SFX", SFX_SERVICE_URL)]:
            r = await _http_client.get(f"{url}/health", timeout=3.0)
            r.raise_for_status()
    except Exception as e:
        raise HTTPException(status_code=503, detail=f"Service unreachable: {e}")

    job = create_job(
        request.video_prompt,
        request.negative_prompt,
        request.audio_prompt,
        request.book_id,
        request.speed,
        request.gap_between_lines_ms,
    )

    # Fire background task (runs independently of the request)
    asyncio.create_task(
        run_compose_job(
            job,
            _http_client,
            request.book_id,
            request.speed,
            request.gap_between_lines_ms,
        )
    )

    return JobStatus(
        job_id=job.job_id,
        status=job.status,
        progress=job.progress,
    )


@app.get("/compose-scene/{job_id}", response_model=JobStatus)
async def compose_scene_status(job_id: str):
    """Poll for job progress."""
    from .composer import get_job

    job = get_job(job_id)
    if not job:
        raise HTTPException(status_code=404, detail="Job not found")

    return JobStatus(
        job_id=job.job_id,
        status=job.status,
        progress=job.progress,
        error=job.error,
    )


@app.get("/compose-scene/{job_id}/result")
async def compose_scene_result(job_id: str):
    """Download the finished video."""
    from .composer import get_job

    job = get_job(job_id)
    if not job:
        raise HTTPException(status_code=404, detail="Job not found")
    if job.status == "failed":
        raise HTTPException(status_code=500, detail=job.error or "Unknown error")
    if job.status != "done" or not job.result_path:
        raise HTTPException(status_code=409, detail=f"Not ready. Status: {job.status}")

    def stream_result():
        with open(job.result_path, "rb") as f:
            while True:
                chunk = f.read(65536)
                if not chunk:
                    break
                yield chunk

    return StreamingResponse(stream_result(), media_type="video/mp4")


if __name__ == "__main__":
    import uvicorn

    uvicorn.run("services.mixer.main:app", host="0.0.0.0", port=8003, reload=False)
