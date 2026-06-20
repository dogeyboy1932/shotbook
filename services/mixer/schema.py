from pydantic import BaseModel


class SFXCue(BaseModel):
    timestamp_ms: int
    prompt: str


class ScriptBlock(BaseModel):
    sequence_id: str
    speaker_id: str
    dialogue: str
    sfx_track: list[SFXCue]


class AudioPromptRequest(BaseModel):
    """Request schema for the /audio_prompt endpoint."""

    audio_prompt: str
    book_id: str = "audio_prompt"
    gap_between_lines_ms: int = 800
