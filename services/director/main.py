from contextlib import asynccontextmanager

import httpx
from fastapi import FastAPI, HTTPException
from fastapi.middleware.cors import CORSMiddleware

from .model import generate_script, set_client, close_client
from .schema import HighlightRequest, ScriptBlock


@asynccontextmanager
async def lifespan(app: FastAPI):
    client = httpx.AsyncClient()
    set_client(client)
    yield
    await client.aclose()
    close_client()


app = FastAPI(title="LoreStream Director Core", lifespan=lifespan)

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_methods=["*"],
    allow_headers=["*"],
)


@app.get("/health")
async def health():
    return {"status": "ok"}


@app.post("/script", response_model=ScriptBlock)
async def create_script(request: HighlightRequest) -> ScriptBlock:
    try:
        return await generate_script(request)
    except (ValueError, Exception) as exc:
        raise HTTPException(status_code=422, detail=str(exc))
