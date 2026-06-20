import asyncio
import base64
import io
import os

import torch
import torchaudio
from audiocraft.models import AudioGen

from .schema import SFXCue, SFXCueResult

MAX_DURATION = 4.0
DEFAULT_DURATION = 2.0
SAMPLE_RATE = 16000


def load_model() -> AudioGen:
    device_env = os.getenv("SFX_GPU_DEVICE", "cuda:2")
    if not torch.cuda.is_available():
        audiogen_device = "cpu"
    elif ":" in device_env:
        # torch.autocast only accepts 'cuda', not 'cuda:N'; select the GPU via set_device
        torch.cuda.set_device(int(device_env.split(":")[1]))
        audiogen_device = "cuda"
    else:
        audiogen_device = device_env
    model = AudioGen.get_pretrained("facebook/audiogen-medium", device=audiogen_device)
    model.set_generation_params(duration=DEFAULT_DURATION)
    return model


def _tensor_to_wav_b64(tensor: torch.Tensor, sample_rate: int) -> str:
    # tensor shape: (channels, samples) or (samples,)
    if tensor.dim() == 1:
        tensor = tensor.unsqueeze(0)
    # Resample to 16kHz mono if needed
    if sample_rate != SAMPLE_RATE:
        resampler = torchaudio.transforms.Resample(orig_freq=sample_rate, new_freq=SAMPLE_RATE)
        tensor = resampler(tensor)
    if tensor.shape[0] > 1:
        tensor = tensor.mean(dim=0, keepdim=True)
    # Clamp and convert to 16-bit PCM
    tensor = tensor.clamp(-1.0, 1.0)
    pcm = (tensor * 32767).to(torch.int16)
    buf = io.BytesIO()
    torchaudio.save(buf, pcm, SAMPLE_RATE, format="wav")
    buf.seek(0)
    return base64.b64encode(buf.read()).decode("utf-8")


def _generate_sync(model: AudioGen, prompts: list[str]) -> list[torch.Tensor]:
    with torch.no_grad():
        outputs = model.generate(prompts)
    # outputs shape: (batch, channels, samples)
    return [outputs[i] for i in range(outputs.shape[0])]


async def generate_cues(model: AudioGen, cues: list[SFXCue]) -> list[SFXCueResult]:
    prompts = [cue.prompt for cue in cues]
    loop = asyncio.get_event_loop()
    tensors = await loop.run_in_executor(None, _generate_sync, model, prompts)
    sample_rate = model.sample_rate
    results = []
    for cue, tensor in zip(cues, tensors):
        audio_b64 = _tensor_to_wav_b64(tensor, sample_rate)
        num_samples = tensor.shape[-1] if tensor.dim() > 1 else tensor.shape[0]
        duration_ms = int(num_samples / sample_rate * 1000)
        results.append(SFXCueResult(
            timestamp_ms=cue.timestamp_ms,
            audio_b64=audio_b64,
            duration_ms=duration_ms,
        ))
    return results
