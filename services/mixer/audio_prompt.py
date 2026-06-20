"""Parser for the audio_prompt format used to drive audio generation.

The audio_prompt is a text block that describes both ambient sound effects and
multi-character dialogue with voice characteristics. It is designed to be the
direct input to the audio generation backend.

Format::

    Ambient bed: <sfx descriptions separated by commas/periods>
    Dialogue: <speaker> (<tone>, <accent>) [voice: <description>]: "<text>"
              | <speaker> (<tone>, <accent>) [voice: <description>]: "<text>"

Example::

    Ambient bed: Sounds of cracking ice, wind howling, and distant waves.
    Dialogue: The Stranger (curious, foreign_accent) [voice: A sorrowful voice.]:
        "Before I come on board..." | Robert Walton (surprised, direct)
        [voice: An enthusiastic voice.]: "We are on a voyage..."

The | (pipe) character separates dialogue entries. Everything between
Ambient bed: and Dialogue: is treated as ambient SFX descriptions.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field

# ---------------------------------------------------------------------------
# Data structures
# ---------------------------------------------------------------------------


@dataclass
class DialogueLine:
    """A single line of dialogue parsed from the audio prompt."""

    speaker: str
    text: str
    tone: str | None = None
    accent: str | None = None
    voice_description: str | None = None


@dataclass
class ParsedAudioPrompt:
    """Structured result of parsing an audio_prompt string."""

    ambient_descriptions: list[str] = field(default_factory=list)
    dialogue_lines: list[DialogueLine] = field(default_factory=list)


# ---------------------------------------------------------------------------
# Regex patterns
# ---------------------------------------------------------------------------

# Match: Ambient bed: <content> (captures everything until "Dialogue:" or end)
_AMBIENT_RE = re.compile(
    r"Ambient\s*bed\s*:\s*(.+?)(?=\s*Dialogue\s*:\s*|$)", re.IGNORECASE | re.DOTALL
)

# Match: Dialogue: <content> (captures everything after)
_DIALOGUE_SECTION_RE = re.compile(r"Dialogue\s*:\s*(.+)", re.IGNORECASE | re.DOTALL)

# Split dialogue entries on | that is NOT inside double quotes.
_DIALOGUE_SPLIT_RE = re.compile(r"\s*\|\s*(?=\w)")

# Parse a single dialogue entry:
#   Speaker Name (tone, accent) [voice: description]: "text"
_DIALOGUE_ENTRY_RE = re.compile(
    r"""
    ^\s*
    (?P<speaker>[^(]+?)                    # speaker name (non-greedy until '(')
    \s*
    (?:\((?P<tone>[^,)]*)                  # optional (tone, accent)
        (?:,\s*(?P<accent>[^)]*))?\)
    )?
    \s*
    (?:\[voice\s*:\s*(?P<voice_desc>[^\]]*)\])?  # optional [voice: ...]
    \s*
    :\s*
    "(?P<text>[^"]*)"                       # ": "text""
    \s*$
    """,
    re.VERBOSE | re.DOTALL,
)

# Split ambient descriptions on common delimiters: double-dot, comma+and, bare "and", or period before capital
_AMBIENT_SPLIT_RE = re.compile(
    r"\s*(?:\.\s*\.|,\s*(?:and\s+)?|\s+and\s+|\.\s+(?=[A-Z]))\s*"
)


# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------


def parse_audio_prompt(text: str) -> ParsedAudioPrompt:
    """Parse a raw *audio_prompt* string into structured data."""
    result = ParsedAudioPrompt()

    # Extract ambient section
    ambient_match = _AMBIENT_RE.search(text)
    if ambient_match:
        ambient_raw = ambient_match.group(1).strip()
        result.ambient_descriptions = _split_ambient(ambient_raw)

    # Extract dialogue section
    dialogue_match = _DIALOGUE_SECTION_RE.search(text)
    if dialogue_match:
        dialogue_raw = dialogue_match.group(1).strip()
        entries = _DIALOGUE_SPLIT_RE.split(dialogue_raw)
        for entry in entries:
            entry = entry.strip()
            if not entry:
                continue
            parsed = _parse_dialogue_entry(entry)
            if parsed:
                result.dialogue_lines.append(parsed)

    return result


# ---------------------------------------------------------------------------
# Internal helpers
# ---------------------------------------------------------------------------


def _split_ambient(raw: str) -> list[str]:
    """Split ambient text into individual SFX descriptions."""
    cleaned = re.sub(r"^(?:Sounds?\s+of\s+)", "", raw.strip(), flags=re.IGNORECASE)
    parts = _AMBIENT_SPLIT_RE.split(cleaned)
    return [p.strip().rstrip(".") for p in parts if p.strip()]


def _parse_dialogue_entry(entry: str) -> DialogueLine | None:
    """Parse a single dialogue entry."""
    match = _DIALOGUE_ENTRY_RE.match(entry)
    if not match:
        return None

    speaker = match.group("speaker").strip()
    text = match.group("text").strip()

    if not speaker or not text:
        return None

    tone = match.group("tone")
    accent = match.group("accent")
    voice_desc = match.group("voice_desc")

    return DialogueLine(
        speaker=speaker.strip(),
        text=text,
        tone=tone.strip() if tone else None,
        accent=accent.strip() if accent else None,
        voice_description=voice_desc.strip() if voice_desc else None,
    )
