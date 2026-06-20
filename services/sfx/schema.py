from pydantic import BaseModel


class SFXCue(BaseModel):
    timestamp_ms: int
    prompt: str


class SFXRequest(BaseModel):
    sequence_id: str
    sfx_track: list[SFXCue]


class SFXCueResult(BaseModel):
    timestamp_ms: int
    audio_b64: str
    duration_ms: int


class SFXResponse(BaseModel):
    sequence_id: str
    cues: list[SFXCueResult]
