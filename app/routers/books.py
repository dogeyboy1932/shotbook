"""Library + reader endpoints backing the frontend:

- GET  /api/books                          -- library list ("which texts do I have")
- GET  /api/books/{book_id}/paragraphs     -- full paragraph stream for the reader view
- POST /api/generate-context/batch         -- resolve state for a set of paragraph_ids
                                               (the reader's highlighted span, possibly
                                               crossing paragraph boundaries)
- POST /api/compose-scene                  -- consolidate those contexts into one scene,
                                               then LLM-plan its video shot breakdown and
                                               audio prompt
"""
from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.context_compiler import compile_contexts
from app.db import get_db_session
from app.models import Book, Paragraph
from app.scene_composer import compose_scene
from app.schemas import (
    BatchContextRequest,
    BookSummaryPayload,
    ComposeSceneRequest,
    ComposedScenePayload,
    GenerationContextPayload,
    ParagraphPayload,
)
from app.video_prompting import VideoPlanningError, generate_video_plan

router = APIRouter(prefix="/api", tags=["library"])


@router.get("/books", response_model=list[BookSummaryPayload])
async def list_books(session: AsyncSession = Depends(get_db_session)) -> list[BookSummaryPayload]:
    """Every ingested book, with a paragraph count for the library card view."""
    query = (
        select(
            Book.book_id,
            Book.title,
            Book.author,
            Book.ingestion_status,
            func.count(Paragraph.paragraph_id).label("paragraph_count"),
        )
        .outerjoin(Paragraph, Paragraph.book_id == Book.book_id)
        .group_by(Book.book_id, Book.title, Book.author, Book.ingestion_status)
        .order_by(Book.book_id)
    )
    rows = (await session.execute(query)).all()
    return [
        BookSummaryPayload(
            book_id=row.book_id,
            title=row.title,
            author=row.author,
            ingestion_status=row.ingestion_status,
            paragraph_count=row.paragraph_count,
        )
        for row in rows
    ]


@router.get("/books/{book_id}/paragraphs", response_model=list[ParagraphPayload])
async def list_paragraphs(
    book_id: int, session: AsyncSession = Depends(get_db_session)
) -> list[ParagraphPayload]:
    """Full paragraph stream for the reader view, in book order. No Tier 2
    state is resolved here -- this is just the text the reader scrolls
    through and highlights; state is fetched on demand per selection."""
    book_exists = await session.scalar(select(Book.book_id).where(Book.book_id == book_id))
    if book_exists is None:
        raise HTTPException(status_code=404, detail=f"book_id={book_id} not found")

    query = (
        select(
            Paragraph.paragraph_id,
            Paragraph.sequence_index,
            Paragraph.chapter_number,
            Paragraph.raw_text,
        )
        .where(Paragraph.book_id == book_id)
        .order_by(Paragraph.sequence_index)
    )
    rows = (await session.execute(query)).all()
    return [
        ParagraphPayload(
            paragraph_id=row.paragraph_id,
            sequence_index=row.sequence_index,
            chapter_number=row.chapter_number,
            raw_text=row.raw_text,
        )
        for row in rows
    ]


@router.post("/generate-context/batch", response_model=list[GenerationContextPayload])
async def generate_context_batch(
    request: BatchContextRequest, session: AsyncSession = Depends(get_db_session)
) -> list[GenerationContextPayload]:
    """Resolves Tier 1 + Tier 2 state for every paragraph the reader's
    highlighted span overlaps. Paragraph ids that don't exist are silently
    omitted from the response rather than raising, since a selection
    spanning real + nonexistent ids should still return what's resolvable."""
    if not request.paragraph_ids:
        raise HTTPException(status_code=400, detail="paragraph_ids must not be empty")
    return await compile_contexts(session, request.paragraph_ids)


@router.post("/compose-scene", response_model=ComposedScenePayload)
async def compose_scene_endpoint(
    request: ComposeSceneRequest,
    session: AsyncSession = Depends(get_db_session),
) -> ComposedScenePayload:
    """Consolidation step: fetches state for every paragraph in the request,
    merges them into one scene description, then has the Claude API plan
    that scene's video shot breakdown (one prompt per distinct shot) on top
    of the deterministic audio prompt."""
    if not request.paragraph_ids:
        raise HTTPException(status_code=400, detail="paragraph_ids must not be empty")

    payloads = await compile_contexts(session, request.paragraph_ids)
    if not payloads:
        raise HTTPException(
            status_code=404,
            detail=f"none of paragraph_ids={request.paragraph_ids} were found",
        )
    scene = compose_scene(payloads)
    try:
        video_plan = await generate_video_plan(scene)
    except VideoPlanningError as exc:
        raise HTTPException(status_code=503, detail=str(exc)) from exc
    return scene.model_copy(update={"video": video_plan})
