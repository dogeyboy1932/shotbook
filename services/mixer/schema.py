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
    speed: float = 1.0  # 1.0 = normal, 1.25 = 25% faster, 0.75 = slower


class MuxRequest(BaseModel):
    """Combine existing video with generated audio."""

    video_path: str  # absolute path to MP4 on server
    audio_prompt: str | None = None  # generate audio from prompt
    audio_path: str | None = None  # or use existing audio file
    speed: float = 1.0
    gap_between_lines_ms: int = 800
    book_id: str = "mux"
