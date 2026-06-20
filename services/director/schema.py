from pydantic import BaseModel


class SFXCue(BaseModel):
    timestamp_ms: int
    prompt: str


class ScriptBlock(BaseModel):
    sequence_id: str
    speaker_id: str
    dialogue: str
    sfx_track: list[SFXCue]


class HighlightRequest(BaseModel):
    highlight: str
    context_chunks: list[str]
    speaker_hint: str | None = None
    book_id: str
