"""Tests for the audio_prompt parser and multi-dialogue mixer pipeline."""

from __future__ import annotations

import pytest


class TestParseAudioPrompt:
    def test_full_format(self):
        from services.mixer.audio_prompt import parse_audio_prompt

        text = (
            "Ambient bed: Sounds of cracking ice, wind howling, and distant waves."
            " Dialogue: The Stranger (curious)"
            ' [voice: Sorrowful.]: "Before I come on board,"'
            " | Robert Walton (surprised)"
            ' [voice: Enthusiastic.]: "We are on a voyage."'
        )
        result = parse_audio_prompt(text)
        assert "cracking ice" in result.ambient_descriptions
        assert len(result.dialogue_lines) == 2
        assert result.dialogue_lines[0].speaker == "The Stranger"
        assert result.dialogue_lines[1].speaker == "Robert Walton"

    def test_ambient_only(self):
        from services.mixer.audio_prompt import parse_audio_prompt

        result = parse_audio_prompt("Ambient bed: rain, thunder, wind.")
        assert len(result.ambient_descriptions) == 3

    def test_dialogue_only(self):
        from services.mixer.audio_prompt import parse_audio_prompt

        result = parse_audio_prompt('Dialogue: Narrator (grimly): "The door."')
        assert len(result.dialogue_lines) == 1
        assert result.dialogue_lines[0].tone == "grimly"

    def test_no_tone_no_voice(self):
        from services.mixer.audio_prompt import parse_audio_prompt

        result = parse_audio_prompt('Dialogue: Someone: "Just a line."')
        line = result.dialogue_lines[0]
        assert line.tone is None

    def test_voice_no_tone(self):
        from services.mixer.audio_prompt import parse_audio_prompt

        text = 'Dialogue: Elder [voice: A deep voice.]: "The old ways."'
        result = parse_audio_prompt(text)
        line = result.dialogue_lines[0]
        assert line.voice_description == "A deep voice."

    def test_empty_input(self):
        from services.mixer.audio_prompt import parse_audio_prompt

        result = parse_audio_prompt("")
        assert len(result.ambient_descriptions) == 0


class TestBuildFilterCmd:
    def test_vocal_only(self):
        from services.mixer.multi_mixer import _build_filter_cmd

        cmd = _build_filter_cmd(vocal_fd=3, sfx_fd=None, total_duration_ms=2000)
        pipe_inputs = [
            cmd[j + 1]
            for j, a in enumerate(cmd)
            if a == "-i" and cmd[j + 1].startswith("pipe:")
        ]
        assert len(pipe_inputs) == 1
        assert "libmp3lame" in cmd

    def test_vocal_plus_sfx(self):
        from services.mixer.multi_mixer import _build_filter_cmd

        cmd = _build_filter_cmd(vocal_fd=3, sfx_fd=5, total_duration_ms=5000)
        pipe_inputs = [
            cmd[j + 1]
            for j, a in enumerate(cmd)
            if a == "-i" and cmd[j + 1].startswith("pipe:")
        ]
        assert len(pipe_inputs) == 2
        filter_str = cmd[cmd.index("-filter_complex") + 1]
        assert "amix" in filter_str

    def test_concat_dialogues(self):
        from services.mixer.multi_mixer import _concat_dialogues

        pcm1 = bytes([1, 2]) * 100
        pcm2 = bytes([3, 4]) * 100
        combined = _concat_dialogues([pcm1, pcm2], gap_ms=500)
        assert len(combined) > 400


@pytest.mark.anyio
async def test_dispatch_from_prompt_mocked(monkeypatch):
    import httpx

    from services.mixer.orchestrator import dispatch_from_prompt

    async def mock_tts(client, dialogue, speaker_id, sequence_id):
        return bytes(22050 * 2)

    async def mock_sfx(client, sequence_id, sfx_track):
        return bytes(0) if not sfx_track else bytes(44100 * 2)

    monkeypatch.setattr("services.mixer.orchestrator._collect_tts", mock_tts)
    monkeypatch.setattr("services.mixer.orchestrator._collect_sfx_pcm", mock_sfx)
    prompt = 'Dialogue: A: "Hello." | A: "Again." | B: "Hi."'
    async with httpx.AsyncClient() as client:
        pcms, sfx, total, texts, names = await dispatch_from_prompt(client, prompt)
    assert len(pcms) == 2  # two speaker groups (A has 2 lines batched)
    assert len(texts) == 2
    assert names == ["A", "B"]
    assert total > 0


def test_audio_prompt_request_schema():
    from services.mixer.schema import AudioPromptRequest

    req = AudioPromptRequest(
        audio_prompt="test", book_id="b1", gap_between_lines_ms=500
    )
    assert req.book_id == "b1"
