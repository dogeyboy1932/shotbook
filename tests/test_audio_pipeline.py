"""Integration tests for the LoreStream AI audio pipeline.

All external dependencies (GPU, network, SGLang, AudioGen) are mocked.
Run sync-only tests with:
    python -m pytest tests/ -v -k "not anyio"
Run all tests (requires anyio + httpx) with:
    python -m pytest tests/ -v
"""
from __future__ import annotations

import base64
import io
import json
import wave

import pytest

# ---------------------------------------------------------------------------
# 1. Schema round-trips
# ---------------------------------------------------------------------------


def test_script_block_schema():
    from services.director.schema import ScriptBlock, SFXCue

    data = {
        "sequence_id": "clip_abc_00000001",
        "speaker_id": "narrator",
        "dialogue": "The forest whispered with ancient secrets.",
        "sfx_track": [
            {"timestamp_ms": 500, "prompt": "wind through trees"},
            {"timestamp_ms": 2000, "prompt": "owl hooting"},
        ],
    }
    block = ScriptBlock.model_validate(data)
    assert block.sequence_id == "clip_abc_00000001"
    assert block.speaker_id == "narrator"
    assert len(block.sfx_track) == 2
    assert block.sfx_track[0].timestamp_ms == 500
    assert block.sfx_track[1].prompt == "owl hooting"


def test_highlight_request_schema():
    from services.director.schema import HighlightRequest

    req = HighlightRequest(
        highlight="The dragon descended.",
        context_chunks=["Chapter 1 ...", "Chapter 2 ..."],
        book_id="book_001",
    )
    assert req.speaker_hint is None

    req_with_hint = HighlightRequest(
        highlight="She said grimly.",
        context_chunks=[],
        speaker_hint="elder_voice",
        book_id="book_002",
    )
    assert req_with_hint.speaker_hint == "elder_voice"


def test_sfx_request_schema():
    from services.sfx.schema import SFXCue, SFXRequest

    req = SFXRequest(
        sequence_id="seq_42",
        sfx_track=[
            SFXCue(timestamp_ms=100, prompt="thunder crack"),
            SFXCue(timestamp_ms=800, prompt="rain on roof"),
        ],
    )
    assert req.sequence_id == "seq_42"
    assert len(req.sfx_track) == 2
    assert req.sfx_track[0].prompt == "thunder crack"
    assert req.sfx_track[1].timestamp_ms == 800


def test_sfx_response_schema():
    from services.sfx.schema import SFXCueResult, SFXResponse

    dummy_b64 = base64.b64encode(b"fakeaudiodata").decode()
    resp = SFXResponse(
        sequence_id="seq_42",
        cues=[
            SFXCueResult(timestamp_ms=100, audio_b64=dummy_b64, duration_ms=2000),
            SFXCueResult(timestamp_ms=800, audio_b64=dummy_b64, duration_ms=1500),
        ],
    )
    assert resp.sequence_id == "seq_42"
    assert len(resp.cues) == 2
    assert resp.cues[0].audio_b64 == dummy_b64
    assert resp.cues[1].duration_ms == 1500


# ---------------------------------------------------------------------------
# 2. TTS tone marker stripping
# ---------------------------------------------------------------------------


def test_tone_marker_stripping():
    from services.tts.fish_audio import _extract_tone_and_clean

    clean, style = _extract_tone_and_clean("[grimly] The shadow approaches.")
    assert clean == "The shadow approaches."
    assert style == "serious"


def test_tone_marker_stripping_whispered():
    from services.tts.fish_audio import _extract_tone_and_clean

    clean, style = _extract_tone_and_clean("[whispered] Come closer.")
    assert clean == "Come closer."
    assert style == "soft"


def test_unknown_tone_marker():
    from services.tts.fish_audio import _extract_tone_and_clean

    # An unmapped marker should strip the brackets and return None for style
    clean, style = _extract_tone_and_clean("[mysteriously] Something lurks.")
    assert clean == "Something lurks."
    assert style is None  # graceful fallback — no crash


def test_no_tone_marker():
    from services.tts.fish_audio import _extract_tone_and_clean

    clean, style = _extract_tone_and_clean("Just a plain sentence.")
    assert clean == "Just a plain sentence."
    assert style is None


# ---------------------------------------------------------------------------
# 3. SFX WAV base64 round-trip
# ---------------------------------------------------------------------------


def _make_silent_wav(duration_s: float = 0.1, sample_rate: int = 16000) -> bytes:
    """Create a minimal valid WAV file in memory (silent mono 16-bit PCM)."""
    num_samples = int(duration_s * sample_rate)
    buf = io.BytesIO()
    with wave.open(buf, "wb") as wf:
        wf.setnchannels(1)
        wf.setsampwidth(2)  # 16-bit
        wf.setframerate(sample_rate)
        wf.writeframes(b"\x00\x00" * num_samples)
    buf.seek(0)
    return buf.read()


def test_wav_b64_round_trip():
    raw_wav = _make_silent_wav(duration_s=0.1, sample_rate=16000)

    # Encode
    encoded = base64.b64encode(raw_wav).decode("utf-8")
    assert isinstance(encoded, str)
    assert len(encoded) > 0

    # Decode and verify it's a valid WAV
    decoded = base64.b64decode(encoded)
    buf = io.BytesIO(decoded)
    with wave.open(buf, "rb") as wf:
        assert wf.getnchannels() == 1
        assert wf.getsampwidth() == 2
        assert wf.getframerate() == 16000
        assert wf.getnframes() == int(0.1 * 16000)


# ---------------------------------------------------------------------------
# 4. Director endpoint (mock SGLang)
# ---------------------------------------------------------------------------


@pytest.mark.anyio
async def test_director_script_endpoint(monkeypatch):
    import httpx
    import uuid
    import services.director.main as director_main
    from services.director.schema import ScriptBlock, SFXCue

    # main.py imports generate_script *by name* from .model, so we must patch
    # it in the services.director.main namespace (not just the model module).
    async def mock_generate_script(request):
        return ScriptBlock(
            sequence_id=f"clip_{request.book_id}_{uuid.uuid4().hex[:8]}",
            speaker_id="narrator",
            dialogue="[grimly] The old tower crumbled.",
            sfx_track=[SFXCue(timestamp_ms=300, prompt="stone falling")],
        )

    monkeypatch.setattr(director_main, "generate_script", mock_generate_script)

    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=director_main.app), base_url="http://test"
    ) as client:
        response = await client.post(
            "/script",
            json={
                "highlight": "The tower falls.",
                "context_chunks": ["context line 1"],
                "book_id": "book_xyz",
            },
        )

    assert response.status_code == 200
    data = response.json()
    assert data["speaker_id"] == "narrator"
    assert "[grimly]" in data["dialogue"]
    assert len(data["sfx_track"]) == 1
    assert "sequence_id" in data


# ---------------------------------------------------------------------------
# 5. SFX endpoint (mock AudioGen model)
# ---------------------------------------------------------------------------


def _stub_sfx_heavy_deps():
    """Inject lightweight stubs for torch/torchaudio/audiocraft before importing sfx.audiogen."""
    import sys
    import types

    # Stub torch — must include Tensor (used as a type annotation at function def time)
    if "torch" not in sys.modules:
        torch_stub = types.ModuleType("torch")
        torch_stub.cuda = types.SimpleNamespace(is_available=lambda: False)
        torch_stub.no_grad = lambda: __import__("contextlib").nullcontext()
        torch_stub.int16 = None  # dtype sentinel used in audiogen
        # Tensor stub with the dim/shape/unsqueeze/clamp/mean interface
        class _FakeTensor:
            def __init__(self, data=None):
                self._data = data or []
            def dim(self): return 1
            @property
            def shape(self): return (0,)
            def unsqueeze(self, dim): return self
            def clamp(self, *a, **kw): return self
            def mean(self, *a, **kw): return self
            def to(self, dtype): return self
        torch_stub.Tensor = _FakeTensor
        sys.modules["torch"] = torch_stub

    # Stub torchaudio
    if "torchaudio" not in sys.modules:
        torchaudio_stub = types.ModuleType("torchaudio")
        transforms_stub = types.ModuleType("torchaudio.transforms")
        transforms_stub.Resample = lambda **kw: (lambda t: t)
        torchaudio_stub.transforms = transforms_stub
        torchaudio_stub.save = lambda *a, **kw: None
        sys.modules["torchaudio"] = torchaudio_stub
        sys.modules["torchaudio.transforms"] = transforms_stub

    # Stub audiocraft
    if "audiocraft" not in sys.modules:
        audiocraft_stub = types.ModuleType("audiocraft")
        models_stub = types.ModuleType("audiocraft.models")
        models_stub.AudioGen = object
        audiocraft_stub.models = models_stub
        sys.modules["audiocraft"] = audiocraft_stub
        sys.modules["audiocraft.models"] = models_stub


@pytest.mark.anyio
async def test_sfx_generate_endpoint(monkeypatch):
    import sys
    import httpx

    _stub_sfx_heavy_deps()

    # Force re-import of sfx modules now that stubs are in place
    for mod in ["services.sfx.audiogen", "services.sfx.main"]:
        sys.modules.pop(mod, None)

    from services.sfx import audiogen
    from services.sfx.main import app
    from services.sfx.schema import SFXCueResult

    dummy_b64 = base64.b64encode(_make_silent_wav()).decode()

    async def mock_generate_cues(model, cues):
        return [
            SFXCueResult(
                timestamp_ms=cue.timestamp_ms,
                audio_b64=dummy_b64,
                duration_ms=100,
            )
            for cue in cues
        ]

    # services/sfx/main.py does `from .audiogen import generate_cues`, so we
    # must patch the name in the main module's namespace, not just in audiogen.
    import services.sfx.main as sfx_main
    monkeypatch.setattr(sfx_main, "generate_cues", mock_generate_cues)

    # The sfx lifespan sets app.state.audiogen = load_model().
    # Seed it directly so the endpoint can read it without running the real lifespan.
    sentinel_model = object()
    app.state.audiogen = sentinel_model

    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=app), base_url="http://test"
    ) as client:
        response = await client.post(
            "/generate",
            json={
                "sequence_id": "seq_test",
                "sfx_track": [
                    {"timestamp_ms": 100, "prompt": "thunder"},
                    {"timestamp_ms": 900, "prompt": "rain"},
                ],
            },
        )

    assert response.status_code == 200
    data = response.json()
    assert data["sequence_id"] == "seq_test"
    assert len(data["cues"]) == 2
    assert data["cues"][0]["audio_b64"] == dummy_b64
    assert data["cues"][1]["audio_b64"] == dummy_b64


# ---------------------------------------------------------------------------
# 6. Health checks (all four services)
# ---------------------------------------------------------------------------


@pytest.mark.anyio
async def test_director_health():
    import httpx
    from services.director.main import app

    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=app), base_url="http://test"
    ) as client:
        resp = await client.get("/health")
    assert resp.status_code == 200
    assert resp.json() == {"status": "ok"}


@pytest.mark.anyio
async def test_tts_health():
    import httpx
    from services.tts.main import app

    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=app), base_url="http://test"
    ) as client:
        resp = await client.get("/health")
    assert resp.status_code == 200
    assert resp.json() == {"status": "ok"}


@pytest.mark.anyio
async def test_sfx_health(monkeypatch):
    import sys
    import httpx

    _stub_sfx_heavy_deps()

    for mod in ["services.sfx.audiogen", "services.sfx.main"]:
        sys.modules.pop(mod, None)

    from services.sfx import audiogen
    from services.sfx.main import app

    monkeypatch.setattr(audiogen, "load_model", lambda: object())

    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=app), base_url="http://test"
    ) as client:
        resp = await client.get("/health")
    assert resp.status_code == 200
    assert resp.json()["status"] == "ok"


@pytest.mark.anyio
async def test_mixer_health():
    import httpx
    from services.mixer.main import app

    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=app), base_url="http://test"
    ) as client:
        resp = await client.get("/health")
    assert resp.status_code == 200
    assert resp.json() == {"status": "ok"}
