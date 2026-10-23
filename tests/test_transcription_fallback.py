import asyncio
from unittest.mock import AsyncMock
import pytest
from tgbridge import voice


@pytest.mark.parametrize("primary,expected,calls", [("hello", "hello", 1), (None, "fallback", 2)])
def test_transcription_fallback_after_failure_only(monkeypatch, primary, expected, calls):
    monkeypatch.setattr(voice, "OPENAI_API_KEY", "openai-fixture")
    monkeypatch.setattr(voice, "GROQ_API_KEY", "groq-fixture")
    whisper = AsyncMock(side_effect=[primary, "fallback"])
    monkeypatch.setattr(voice, "_whisper", whisper)
    assert asyncio.run(voice.transcribe("fixture.ogg")) == expected
    assert whisper.await_count == calls
    assert "api.openai.com" in whisper.call_args_list[0].args[0]
    if calls == 2:
        assert "api.groq.com" in whisper.call_args_list[1].args[0]


def test_transcription_without_configured_keys_does_not_call_network(monkeypatch):
    monkeypatch.setattr(voice, "OPENAI_API_KEY", "")
    monkeypatch.setattr(voice, "GROQ_API_KEY", "")
    whisper = AsyncMock()
    monkeypatch.setattr(voice, "_whisper", whisper)
    assert asyncio.run(voice.transcribe("fixture.ogg")) is None
    whisper.assert_not_called()
