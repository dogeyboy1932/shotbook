from contextlib import asynccontextmanager

import uvicorn
from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware

from .audiogen import generate_cues, load_model
from .schema import SFXRequest, SFXResponse


@asynccontextmanager
async def lifespan(app: FastAPI):
    app.state.audiogen = load_model()
    yield


app = FastAPI(lifespan=lifespan)

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_methods=["*"],
    allow_headers=["*"],
)


@app.post("/generate", response_model=SFXResponse)
async def generate(request: SFXRequest) -> SFXResponse:
    cues = await generate_cues(app.state.audiogen, request.sfx_track)
    return SFXResponse(sequence_id=request.sequence_id, cues=cues)


@app.get("/health")
async def health():
    return {"status": "ok", "model": "facebook/audiogen-medium"}


if __name__ == "__main__":
    uvicorn.run("main:app", host="0.0.0.0", port=8002, reload=False)
