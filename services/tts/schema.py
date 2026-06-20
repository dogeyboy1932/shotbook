from pydantic import BaseModel


class SynthesizeRequest(BaseModel):
    dialogue: str
    speaker_id: str
    sequence_id: str
