"""Tests for the audio_prompt parser and multi-dialogue mixer pipeline.

All external dependencies (GPU, network, TTS, SFX) are mocked.
Run with:
    python -m pytest tests/test_audio_prompt.py -v
"""

from __future__ import annotations

import pytest

# ============================================================================
# 1. Audio prompt parser tests
# ============================================================================


class TestParseAudioPrompt:
    """Tests for services.mixer.audio_prompt.parse_audio_prompt()."""

    def test_full_format(self):
        from services.mixer.audio_prompt import parse_audio_prompt

        text = (
            "Ambient bed: Sounds of cracking ice, wind howling, and distant waves."
            " Dialogue: The Stranger (curious, foreign_accent)"
            ' [voice: A sorrowful voice.]: "Before I come on board your vessel,'
            ' will you have the kindness to inform me whither you are bound?"'
            " | Robert Walton (surprised, direct)"
            " [voice: An enthusiastic voice.]:"
            ' "We are on a voyage of discovery towards the northern pole."'
        )

        result = parse_audio_prompt(text)

        assert "cracking ice" in result.ambient_descriptions
        assert "wind howling" in result.ambient_descriptions
        assert "distant waves" in result.ambient_descriptions

        assert len(result.dialogue_lines) == 2

        line0 = result.dialogue_lines[0]
        assert line0.speaker == "The Stranger"
        assert line0.tone == "curious"
        assert line0.accent == "foreign_accent"
        assert "sorrowful" in (line0.voice_description or "")
        assert "Before I come on board" in line0.text

        line1 = result.dialogue_lines[1]
        assert line1.speaker == "Robert Walton"
        assert line1.tone == "surprised"
        assert line1.accent == "direct"
        assert "enthusiastic" in (line1.voice_description or "")
        assert "voyage of discovery" in line1.text

    def test_ambient_only(self):
        from services.mixer.audio_prompt import parse_audio_prompt

        text = "Ambient bed: rain falling, thunder rolling, wind gusting."
        result = parse_audio_prompt(text)

        assert len(result.ambient_descriptions) == 3
        assert "rain falling" in result.ambient_descriptions
        assert len(result.dialogue_lines) == 0

    def test_dialogue_only(self):
        from services.mixer.audio_prompt import parse_audio_prompt

        text = 'Dialogue: Narrator (grimly): "The door creaked open."'
        result = parse_audio_prompt(text)

        assert len(result.ambient_descriptions) == 0
        assert len(result.dialogue_lines) == 1
        assert result.dialogue_lines[0].speaker == "Narrator"
        assert result.dialogue_lines[0].tone == "grimly"
        assert result.dialogue_lines[0].text == "The door creaked open."

    def test_dialogue_no_tone_no_voice(self):
        from services.mixer.audio_prompt import parse_audio_prompt

        text = 'Dialogue: Someone: "Just a plain line."'
        result = parse_audio_prompt(text)

        line = result.dialogue_lines[0]
        assert line.speaker == "Someone"
        assert line.text == "Just a plain line."
        assert line.tone is None
        assert line.accent is None
        assert line.voice_description is None

    def test_dialogue_tone_no_voice(self):
        from services.mixer.audio_prompt import parse_audio_prompt

        text = 'Dialogue: Ghost (ominously): "Beware..."'
        result = parse_audio_prompt(text)

        line = result.dialogue_lines[0]
        assert line.speaker == "Ghost"
        assert line.tone == "ominously"
        assert line.accent is None
        assert line.voice_description is None
        assert line.text == "Beware..."

    def test_dialogue_voice_no_tone(self):
        from services.mixer.audio_prompt import parse_audio_prompt

        text = (
            "Dialogue: Elder [voice: A deep, weathered voice.]:"
            ' "The old ways are forgotten."'
        )
        result = parse_audio_prompt(text)

        line = result.dialogue_lines[0]
        assert line.speaker == "Elder"
        assert line.tone is None
        assert line.voice_description == "A deep, weathered voice."
        assert line.text == "The old ways are forgotten."

    def test_dialogue_with_comma_in_voice_description(self):
        from services.mixer.audio_prompt import parse_audio_prompt

        text = (
            "Dialogue: Captain (stern, commanding)"
            " [voice: A voice that alternates between deep sorrow"
            " and passionate eloquence, with a foreign accent.]:"
            ' "All hands on deck."'
        )
        result = parse_audio_prompt(text)

        line = result.dialogue_lines[0]
        assert line.speaker == "Captain"
        assert line.tone == "stern"
        assert line.accent == "commanding"
        assert "deep sorrow" in line.voice_description
        assert "foreign accent" in line.voice_description
        assert line.text == "All hands on deck."

    def test_empty_input(self):
        from services.mixer.audio_prompt import parse_audio_prompt

        result = parse_audio_prompt("")
        assert len(result.ambient_descriptions) == 0
        assert len(result.dialogue_lines) == 0

    def test_ambient_with_double_dot_separator(self):
        from services.mixer.audio_prompt import parse_audio_prompt

        text = "Ambient bed: cracking ice.. wind howling.. distant waves"
        result = parse_audio_prompt(text)

        assert len(result.ambient_descriptions) == 3
        assert "cracking ice" in result.ambient_descriptions
        assert "wind howling" in result.ambient_descriptions
        assert "distant waves" in result.ambient_descriptions

    def test_ambient_strips_sounds_prefix(self):
        from services.mixer.audio_prompt import parse_audio_prompt

        text = "Ambient bed: Sound of rain and wind."
        result = parse_audio_prompt(text)
        assert len(result.ambient_descriptions) == 2
        assert "rain" in result.ambient_descriptions[0]
        assert "wind" in result.ambient_descriptions[1]


# ============================================================================
# 2. Multi-mixer ffmpeg command builder tests
# ============================================================================


class TestBuildFilterCmd:
    """Tests for services.mixer.multi_mixer._build_filter_cmd()."""

    def _make_silent_pcm(self, duration_ms: int) -> bytes:
        """Create silent s16le mono PCM at 44100 Hz for given duration."""
        num_samples = int(44100 * duration_ms / 1000)
        return b"\x00\x00" * num_samples

    def test_single_dialogue_no_sfx(self):
        from services.mixer.multi_mixer import _build_filter_cmd

        pcm = self._make_silent_pcm(1000)
        cmd = _build_filter_cmd(
            read_fds=[3],
            dialogue_pcms=[pcm],
            durations_ms=[1000],
            delays_ms=[0],
            sfx_pcm=None,
            total_duration_ms=1500,
        )

        assert cmd[0] == "ffmpeg"
        assert "-f" in cmd
        assert "s16le" in cmd
        assert "pipe:3" in cmd
        assert "-filter_complex" in cmd
        # Should output AAC in MP4
        assert "-c:a" in cmd
        assert "aac" in cmd
        assert "-f" in cmd
        assert "mp4" in cmd

    def test_two_dialogues_with_sfx(self):
        from services.mixer.multi_mixer import _build_filter_cmd

        pcm1 = self._make_silent_pcm(2000)
        pcm2 = self._make_silent_pcm(1500)
        sfx = self._make_silent_pcm(3000)

        cmd = _build_filter_cmd(
            read_fds=[3, 4, 5],
            dialogue_pcms=[pcm1, pcm2],
            durations_ms=[2000, 1500],
            delays_ms=[0, 2800],
            sfx_pcm=sfx,
            total_duration_ms=5000,
        )

        # Count only -i pipe:N inputs (not output pipe:1)
        pipe_inputs = []
        for j, arg in enumerate(cmd):
            if arg == "-i" and j + 1 < len(cmd) and cmd[j + 1].startswith("pipe:"):
                pipe_inputs.append(cmd[j + 1])
        assert len(pipe_inputs) == 3
        assert "pipe:3" in pipe_inputs
        assert "pipe:4" in pipe_inputs
        assert "pipe:5" in pipe_inputs

        filter_str = cmd[cmd.index("-filter_complex") + 1]
        assert "adelay=0|0" in filter_str
        assert "adelay=2800|2800" in filter_str
        assert "amix" in filter_str
        assert "aloop" in filter_str

    def test_no_dialogues_with_sfx(self):
        from services.mixer.multi_mixer import _build_filter_cmd

        sfx = self._make_silent_pcm(5000)
        cmd = _build_filter_cmd(
            read_fds=[3],
            dialogue_pcms=[],
            durations_ms=[],
            delays_ms=[],
            sfx_pcm=sfx,
            total_duration_ms=5000,
        )

        # Count only -i pipe:N inputs
        pipe_inputs = []
        for j, arg in enumerate(cmd):
            if arg == "-i" and j + 1 < len(cmd) and cmd[j + 1].startswith("pipe:"):
                pipe_inputs.append(cmd[j + 1])
        assert len(pipe_inputs) == 1


# ============================================================================
# 3. Orchestrator dispatch_from_prompt tests (mocked services)
# ============================================================================


@pytest.mark.anyio
async def test_dispatch_from_prompt_mocked(monkeypatch):
    import httpx

    from services.mixer.orchestrator import dispatch_from_prompt

    async def mock_collect_tts(client, dialogue, speaker_id, sequence_id):
        return b"\x00\x00" * 44100

    async def mock_collect_sfx(client, sequence_id, sfx_track):
        if sfx_track:
            return b"\x00\x00" * 44100 * 2
        return b""

    monkeypatch.setattr("services.mixer.orchestrator._collect_tts", mock_collect_tts)
    monkeypatch.setattr(
        "services.mixer.orchestrator._collect_sfx_pcm", mock_collect_sfx
    )

    prompt = (
        "Ambient bed: wind blowing, ice cracking."
        ' Dialogue: Captain (stern): "Ready the ship."'
        ' | First Mate (urgent): "Aye aye!"'
    )

    async with httpx.AsyncClient() as client:
        dialogue_pcms, sfx_pcm, total_ms = await dispatch_from_prompt(
            client, prompt, book_id="test_book"
        )

    assert len(dialogue_pcms) == 2
    assert len(sfx_pcm) > 0
    assert 3000 < total_ms < 4000


@pytest.mark.anyio
async def test_dispatch_from_prompt_dialogue_only(monkeypatch):
    import httpx

    from services.mixer.orchestrator import dispatch_from_prompt

    async def mock_collect_tts(client, dialogue, speaker_id, sequence_id):
        return b"\x00\x00" * 22050

    async def mock_collect_sfx(client, sequence_id, sfx_track):
        return b""

    monkeypatch.setattr("services.mixer.orchestrator._collect_tts", mock_collect_tts)
    monkeypatch.setattr(
        "services.mixer.orchestrator._collect_sfx_pcm", mock_collect_sfx
    )

    prompt = 'Dialogue: Narrator: "Once upon a time."'

    async with httpx.AsyncClient() as client:
        dialogue_pcms, sfx_pcm, total_ms = await dispatch_from_prompt(client, prompt)

    assert len(dialogue_pcms) == 1
    assert sfx_pcm == b""
    assert total_ms == 1000


# ============================================================================
# 4. AudioPromptRequest schema test
# ============================================================================


def test_audio_prompt_request_schema():
    from services.mixer.schema import AudioPromptRequest

    req = AudioPromptRequest(
        audio_prompt="Ambient bed: rain. Dialogue: Man: Hello.",
        book_id="frankenstein_ch1",
        gap_between_lines_ms=1200,
    )
    assert req.audio_prompt == "Ambient bed: rain. Dialogue: Man: Hello."
    assert req.book_id == "frankenstein_ch1"
    assert req.gap_between_lines_ms == 1200

    req2 = AudioPromptRequest(audio_prompt="Dialogue: X: Y.")
    assert req2.book_id == "audio_prompt"
    assert req2.gap_between_lines_ms == 800
