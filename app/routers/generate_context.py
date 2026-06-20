"""On-demand context-compile endpoint.

Given a paragraph_id, resolves the exact character/location state at that
point in the book's timeline and returns one self-contained JSON payload
ready to hand to the video diffusion, XTTS, and SFX pipelines -- no further
database round-trips required downstream.

The temporal resolution (Tier 1 baseline + most-recent applicable Tier 2
delta) lives in app/context_compiler.py, shared with the batch endpoint in
app/routers/books.py.
"""
from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy.ext.asyncio import AsyncSession

from app.context_compiler import compile_contexts
from app.db import get_db_session
from app.schemas import GenerationContextPayload

router = APIRouter(prefix="/api", tags=["generation"])


@router.get("/generate-context/{paragraph_id}", response_model=GenerationContextPayload)
async def generate_context(
    paragraph_id: int, session: AsyncSession = Depends(get_db_session)
) -> GenerationContextPayload:
    """Compiles a single self-contained generation payload for `paragraph_id`."""
    payloads = await compile_contexts(session, [paragraph_id])
    if not payloads:
        raise HTTPException(status_code=404, detail=f"paragraph_id={paragraph_id} not found")
    return payloads[0]
