"""FastAPI application entry point."""
from __future__ import annotations

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware

from app.routers.books import router as books_router
from app.routers.generate_context import router as generate_context_router

app = FastAPI(
    title="book_video_gen",
    description="Compiles self-contained video/audio generation payloads from book paragraph beats.",
    version="1.0.0",
)

# The reader frontend (Vite dev server) runs on a different origin than the API.
app.add_middleware(
    CORSMiddleware,
    allow_origins=["http://localhost:5173", "http://127.0.0.1:5173"],
    allow_methods=["*"],
    allow_headers=["*"],
)

app.include_router(generate_context_router)
app.include_router(books_router)


@app.get("/healthz")
async def healthz() -> dict[str, str]:
    return {"status": "ok"}
