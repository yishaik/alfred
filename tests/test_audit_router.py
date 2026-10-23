import asyncio
from types import SimpleNamespace
from unittest.mock import AsyncMock
import pytest
from tgbridge import handoff, router
from tgbridge.session import AgentConfig, AgentSession


@pytest.fixture
def session(tmp_path, monkeypatch):
    monkeypatch.setattr(handoff, "STATE_DIR", tmp_path)
    cfg = AgentConfig(name="audit", workdir=str(tmp_path))
    mgr = SimpleNamespace(bot=object(), session_ids={}, active="audit", agents={"audit": cfg})
    s = AgentSession(mgr, cfg, "audit@p", 1, 1, None)
    s.outbox = SimpleNamespace(emit=lambda *a: None, start=lambda: None, muted=False)
    s._react = lambda *a: None
    return s


def test_picker_pin_reaches_selected_model_and_falls_back_on_failure(session, monkeypatch):
    monkeypatch.setenv("OPENROUTER_API_KEY", "fixture")
    monkeypatch.setattr(router, "load_config", lambda: router.RouterConfig())
    monkeypatch.setattr(router, "_load_usage", lambda: {"counts": {}})
    monkeypatch.setattr(router, "_bump_usage", lambda *a: None)
    monkeypatch.setattr(router, "_log_decision", lambda *a, **k: None)
    chat = AsyncMock(return_value="selected answer")
    monkeypatch.setattr(router, "_chat", chat)
    session._external_pin = "openai/model-fixture"
    assert asyncio.run(session._maybe_route("hello")) is None
    assert chat.call_args.args[:3] == ("https://openrouter.ai/api/v1", "fixture", "openai/model-fixture")
    assert session._external_pin == ""
    chat.side_effect = RuntimeError("provider down")
    session._external_pin = "openai/model-fixture"
    assert asyncio.run(session._maybe_route("keep original")) == "keep original"


def test_dynamic_model_never_enters_automatic_chain(monkeypatch):
    config = router.RouterConfig(providers=[])
    monkeypatch.setattr(router, "load_config", lambda: config)
    monkeypatch.setattr(router, "_log_decision", lambda *a, **k: None)
    chat = AsyncMock()
    monkeypatch.setattr(router, "_chat", chat)
    assert asyncio.run(router.answer_free("hi", SimpleNamespace(free_history=[]), router.Decision(route="free"))) is None
    chat.assert_not_called()


def test_explicit_model_uses_durable_handoff_and_keeps_long_answer(session, monkeypatch):
    monkeypatch.setenv("OPENROUTER_API_KEY", "fixture")
    monkeypatch.setattr(router, "load_config", lambda: router.RouterConfig())
    monkeypatch.setattr(router, "_load_usage", lambda: {"counts": {}})
    monkeypatch.setattr(router, "_bump_usage", lambda *a: None)
    monkeypatch.setattr(router, "_log_decision", lambda *a, **k: None)
    session.handoff.record("claude", "earlier", "durable detail " + "x" * 2000 + " tail decision")
    session.free_history.clear()
    answer = "answer " * 400
    chat = AsyncMock(return_value=answer)
    monkeypatch.setattr(router, "_chat", chat)
    decision = router.Decision(route="free", provider="openrouter", model_id="openai/fixture")
    result = asyncio.run(router.answer_free("continue", session, decision))
    assert result[0] == answer.strip()
    sent = chat.call_args.args[3][-1]["content"]
    assert "durable detail" in sent and "tail decision" in sent
    assert chat.call_args.kwargs["max_tokens"] == 4096
