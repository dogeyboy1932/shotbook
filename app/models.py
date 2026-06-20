"""SQLAlchemy 2.0 ORM models mirroring db/schema.sql exactly.

These are used by the ingestion orchestrator for writes. The FastAPI
read-path endpoint bypasses the ORM in favor of one hand-tuned SQL query
(see app/routers/generate_context.py) because the temporal-resolution join
is cheaper to express and faster to execute as raw LATERAL SQL than as
ORM-generated queries.
"""
from __future__ import annotations

import datetime as dt

from sqlalchemy import (
    BigInteger,
    CheckConstraint,
    ForeignKey,
    Integer,
    String,
    Text,
    UniqueConstraint,
)
from sqlalchemy.dialects.postgresql import ARRAY, JSONB
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column, relationship
from sqlalchemy.sql import func


class Base(DeclarativeBase):
    pass


class Book(Base):
    __tablename__ = "books"

    book_id: Mapped[int] = mapped_column(BigInteger, primary_key=True)
    title: Mapped[str] = mapped_column(Text, nullable=False)
    author: Mapped[str | None] = mapped_column(Text)
    source_uri: Mapped[str | None] = mapped_column(Text)
    ingestion_status: Mapped[str] = mapped_column(Text, nullable=False, default="pending")
    created_at: Mapped[dt.datetime] = mapped_column(server_default=func.now())

    characters: Mapped[list["Character"]] = relationship(back_populates="book")
    locations: Mapped[list["Location"]] = relationship(back_populates="book")
    paragraphs: Mapped[list["Paragraph"]] = relationship(back_populates="book")


# ---------------------------------------------------------------------------
# Tier 1: Global Registry
# ---------------------------------------------------------------------------
class Character(Base):
    __tablename__ = "characters"
    __table_args__ = (UniqueConstraint("book_id", "canonical_name", name="uq_characters_book_name"),)

    character_id: Mapped[int] = mapped_column(BigInteger, primary_key=True)
    book_id: Mapped[int] = mapped_column(BigInteger, ForeignKey("books.book_id", ondelete="CASCADE"))
    canonical_name: Mapped[str] = mapped_column(Text, nullable=False)
    aliases: Mapped[list[str]] = mapped_column(ARRAY(Text), nullable=False, default=list)
    baseline_visual_description: Mapped[str] = mapped_column(Text, nullable=False)
    baseline_voice_description: Mapped[str] = mapped_column(Text, nullable=False)
    voice_reference_audio_uri: Mapped[str | None] = mapped_column(Text)
    extended_profile: Mapped[dict] = mapped_column(JSONB, nullable=False, default=dict)
    created_at: Mapped[dt.datetime] = mapped_column(server_default=func.now())

    book: Mapped[Book] = relationship(back_populates="characters")
    states: Mapped[list["CharacterState"]] = relationship(back_populates="character")


class Location(Base):
    __tablename__ = "locations"
    __table_args__ = (UniqueConstraint("book_id", "canonical_name", name="uq_locations_book_name"),)

    location_id: Mapped[int] = mapped_column(BigInteger, primary_key=True)
    book_id: Mapped[int] = mapped_column(BigInteger, ForeignKey("books.book_id", ondelete="CASCADE"))
    canonical_name: Mapped[str] = mapped_column(Text, nullable=False)
    aliases: Mapped[list[str]] = mapped_column(ARRAY(Text), nullable=False, default=list)
    baseline_visual_description: Mapped[str] = mapped_column(Text, nullable=False)
    baseline_ambient_sfx_prompt: Mapped[str] = mapped_column(Text, nullable=False)
    extended_profile: Mapped[dict] = mapped_column(JSONB, nullable=False, default=dict)
    created_at: Mapped[dt.datetime] = mapped_column(server_default=func.now())

    book: Mapped[Book] = relationship(back_populates="locations")
    states: Mapped[list["LocationState"]] = relationship(back_populates="location")


# ---------------------------------------------------------------------------
# Tier 3: Paragraph Beats
# ---------------------------------------------------------------------------
class Paragraph(Base):
    __tablename__ = "paragraphs"
    __table_args__ = (
        UniqueConstraint("book_id", "sequence_index", name="uq_paragraphs_book_sequence"),
        CheckConstraint(
            "camera_framing IN ('extreme_close_up','close_up','medium_shot','wide_shot',"
            "'establishing_shot','over_the_shoulder','pov')",
            name="chk_camera_framing",
        ),
    )

    paragraph_id: Mapped[int] = mapped_column(BigInteger, primary_key=True)
    book_id: Mapped[int] = mapped_column(BigInteger, ForeignKey("books.book_id", ondelete="CASCADE"))
    chapter_number: Mapped[int] = mapped_column(Integer, nullable=False)
    sequence_index: Mapped[int] = mapped_column(Integer, nullable=False)
    raw_text: Mapped[str] = mapped_column(Text, nullable=False)
    active_location_id: Mapped[int | None] = mapped_column(BigInteger, ForeignKey("locations.location_id"))
    camera_framing: Mapped[str] = mapped_column(String(32), nullable=False, default="medium_shot")
    action_summary: Mapped[str] = mapped_column(Text, nullable=False)
    dialogue_script: Mapped[list] = mapped_column(JSONB, nullable=False, default=list)
    sfx_prompts: Mapped[list] = mapped_column(JSONB, nullable=False, default=list)
    created_at: Mapped[dt.datetime] = mapped_column(server_default=func.now())

    book: Mapped[Book] = relationship(back_populates="paragraphs")
    active_location: Mapped[Location | None] = relationship()
    active_characters: Mapped[list[Character]] = relationship(
        secondary="paragraph_characters", lazy="selectin"
    )


class ParagraphCharacter(Base):
    __tablename__ = "paragraph_characters"

    paragraph_id: Mapped[int] = mapped_column(
        BigInteger, ForeignKey("paragraphs.paragraph_id", ondelete="CASCADE"), primary_key=True
    )
    character_id: Mapped[int] = mapped_column(
        BigInteger, ForeignKey("characters.character_id", ondelete="CASCADE"), primary_key=True
    )


# ---------------------------------------------------------------------------
# Tier 2: Temporal Ledger
# ---------------------------------------------------------------------------
class CharacterState(Base):
    __tablename__ = "character_states"

    state_id: Mapped[int] = mapped_column(BigInteger, primary_key=True)
    character_id: Mapped[int] = mapped_column(BigInteger, ForeignKey("characters.character_id", ondelete="CASCADE"))
    valid_from_paragraph_id: Mapped[int] = mapped_column(
        BigInteger, ForeignKey("paragraphs.paragraph_id", ondelete="CASCADE")
    )
    valid_until_paragraph_id: Mapped[int | None] = mapped_column(
        BigInteger, ForeignKey("paragraphs.paragraph_id", ondelete="CASCADE")
    )
    appearance_delta: Mapped[str | None] = mapped_column(Text)
    emotional_state: Mapped[str | None] = mapped_column(Text)
    vocal_delta_prompt: Mapped[str | None] = mapped_column(Text)
    profile_snapshot: Mapped[dict | None] = mapped_column(JSONB)
    created_at: Mapped[dt.datetime] = mapped_column(server_default=func.now())

    character: Mapped[Character] = relationship(back_populates="states")


class LocationState(Base):
    __tablename__ = "location_states"

    state_id: Mapped[int] = mapped_column(BigInteger, primary_key=True)
    location_id: Mapped[int] = mapped_column(BigInteger, ForeignKey("locations.location_id", ondelete="CASCADE"))
    valid_from_paragraph_id: Mapped[int] = mapped_column(
        BigInteger, ForeignKey("paragraphs.paragraph_id", ondelete="CASCADE")
    )
    valid_until_paragraph_id: Mapped[int | None] = mapped_column(
        BigInteger, ForeignKey("paragraphs.paragraph_id", ondelete="CASCADE")
    )
    atmosphere_delta: Mapped[str | None] = mapped_column(Text)
    lighting_state: Mapped[str | None] = mapped_column(Text)
    ambient_sfx_delta: Mapped[str | None] = mapped_column(Text)
    profile_snapshot: Mapped[dict | None] = mapped_column(JSONB)
    created_at: Mapped[dt.datetime] = mapped_column(server_default=func.now())

    location: Mapped[Location] = relationship(back_populates="states")
