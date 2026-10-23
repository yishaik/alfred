"""Exercise real panel callbacks offline, without Telegram or inference."""
import asyncio
from types import SimpleNamespace
from unittest.mock import AsyncMock, Mock

import pytest

from tgbridge import codex_engine, engine_ui, handlers, model_catalog
from tgbridge.handoff import Conversation
from tgbridge.session import AgentConfig


@pytest.fixture
def session(tmp_path, monkeypatch):
    cfg = AgentConfig(name="panel-test", model="sonnet")
    mgr = SimpleNamespace(save_agents=Mock())
    s = SimpleNamespace(sid=42, skey="panel-test@p", cfg=cfg, mgr=mgr,
                        model="sonnet", engine="claude", busy=False, client=None,
                        connected=False, feed=AsyncMock(), _drain=AsyncMock(),
                        handoff=Conversation("panel-test@p", tmp_path))
    monkeypatch.setattr(engine_ui, "readiness", lambda name: (True, "test"))
    monkeypatch.setattr(codex_engine, "get_model", lambda route: "codex-choice")
    return s


def payloads(markup):
    return [b.callback_data for row in markup.inline_keyboard for b in row]


def tap(monkeypatch, session, data, *, found=True):
    other = SimpleNamespace(engine="claude", feed=AsyncMock())
    mgr = SimpleNamespace(find_by_sid=Mock(return_value=session if found else None),
                          session_for_route=AsyncMock(return_value=other))
    q = SimpleNamespace(data=data, answer=AsyncMock(),
                        edit_message_reply_markup=AsyncMock(),
                        message=SimpleNamespace(message_thread_id=None))
    update = SimpleNamespace(callback_query=q, effective_chat=SimpleNamespace(id=123))
    edit = AsyncMock()
    monkeypatch.setattr(handlers, "authorized_chat", lambda chat: True)
    monkeypatch.setattr(handlers, "mgr", lambda ctx: mgr)
    monkeypatch.setattr(handlers, "_edit", edit)
    asyncio.run(handlers.on_callback(update, SimpleNamespace()))
    return mgr, q, edit, other


def test_panel_engine_opens_control_not_prompt(session):
    data = payloads(handlers.panel_kb(session))
    assert "eng:42:menu" in data
    assert "send:/engine" not in data


def test_picker_shows_provider_current_model_and_buttons(session):
    text, kb = engine_ui.card(session)
    assert "Claude" in text and "sonnet" in text
    assert {"eng:42:claude", "eng:42:codex", "eng:42:antigravity"} <= set(payloads(kb))
    assert any(b.text.startswith("● Claude") for row in kb.inline_keyboard for b in row)


def test_provider_button_is_bound_to_original_session(session, monkeypatch):
    session.handoff.record("claude", "task", "keep this decision")
    mgr, _, edit, other = tap(monkeypatch, session, "eng:42:codex")
    assert session.engine == "codex" and other.engine == "claude"
    assert session.handoff.state["engine"] == "codex"
    restored = Conversation("panel-test@p", session.handoff.journal.parent)
    assert restored.state["engine"] == "codex"
    assert "keep this decision" in restored.prompt("codex", "go")
    mgr.session_for_route.assert_not_awaited()
    session.feed.assert_not_awaited()
    edit.assert_awaited_once()


def test_busy_provider_button_preserves_engine(session, monkeypatch):
    session.busy = True
    _, _, edit, _ = tap(monkeypatch, session, "eng:42:codex")
    assert session.engine == "claude"
    assert "בתהליך" in edit.await_args.args[1]


def test_unavailable_provider_reports_reason_and_preserves_state(session, monkeypatch):
    monkeypatch.setattr(engine_ui, "readiness", lambda name: (False, "CLI missing"))
    ok, reason = engine_ui.select(session, "codex")
    assert not ok and "CLI missing" in reason
    assert session.engine == "claude"
    assert "engine" not in session.handoff.state


def test_native_threads_and_model_pins_survive_switch(session, monkeypatch):
    monkeypatch.setattr(codex_engine, "_threads", {session.skey: "native-thread"})
    monkeypatch.setattr(codex_engine, "_models", {session.skey: "pinned"})
    assert engine_ui.select(session, "codex")[0]
    assert engine_ui.select(session, "claude")[0]
    assert codex_engine.thread_for(session.skey) == "native-thread"
    assert codex_engine._models[session.skey] == "pinned"
    assert session.cfg.model == "sonnet"


def test_stale_panel_does_not_target_active_agent(session, monkeypatch):
    mgr, q, edit, other = tap(monkeypatch, session, "eng:42:codex", found=False)
    assert other.engine == "claude"
    mgr.session_for_route.assert_not_awaited()
    q.edit_message_reply_markup.assert_awaited_once_with(reply_markup=None)
    edit.assert_not_awaited()


def test_legacy_panel_engine_never_sends_command_to_model(session, monkeypatch):
    monkeypatch.setattr(handlers, "_edit", AsyncMock())
    mgr = SimpleNamespace(session_for_route=AsyncMock(return_value=session))
    monkeypatch.setattr(handlers, "mgr", lambda ctx: mgr)
    monkeypatch.setattr(handlers, "authorized_chat", lambda chat: True)
    q = SimpleNamespace(data="send:/engine", answer=AsyncMock(),
                        message=SimpleNamespace(message_thread_id=None))
    asyncio.run(handlers.on_callback(SimpleNamespace(callback_query=q,
                effective_chat=SimpleNamespace(id=123)), SimpleNamespace()))
    session.feed.assert_not_awaited()
    handlers._edit.assert_awaited_once()


def test_model_picker_from_engine_panel_stays_session_bound(session, monkeypatch):
    session.engine = "codex"
    session.model_catalog = {"codex": [("new-model", "New model")]}
    monkeypatch.setattr(model_catalog, "refresh", AsyncMock())
    _, _, edit, _ = tap(monkeypatch, session, "eng:42:models")
    data = payloads(edit.await_args.args[2])
    assert "eng:42:pick:cmodel:new-model" in data
    assert "eng:42:models" in data and "eng:42:menu" in data
    assert all(len(d.encode()) <= 64 for d in data)


def test_model_choice_targets_original_route(session, monkeypatch):
    session.engine = "codex"
    session.model_catalog = {"codex": [("new-model", "New model")]}
    setter = Mock()
    monkeypatch.setattr(codex_engine, "set_model", setter)
    tap(monkeypatch, session, "eng:42:pick:cmodel:new-model")
    setter.assert_called_once_with(session.skey, "new-model")


def test_stale_model_picker_cannot_change_new_engine(session, monkeypatch):
    setter = Mock()
    monkeypatch.setattr(codex_engine, "set_model", setter)
    tap(monkeypatch, session, "eng:42:pick:cmodel:new-model")
    setter.assert_not_called()


def test_command_and_panel_share_transition(session, monkeypatch):
    monkeypatch.setattr(handlers, "_session", AsyncMock(return_value=session))
    reply = AsyncMock()
    asyncio.run(handlers.cmd_engine(SimpleNamespace(message=SimpleNamespace(reply_text=reply)),
                                   SimpleNamespace(args=["codex"])))
    assert session.engine == "codex"
    assert "eng:42:claude" in payloads(reply.await_args.kwargs["reply_markup"])


def test_panel_model_label_follows_active_engine(session):
    session.engine = "codex"
    buttons = [b for row in handlers.panel_kb(session).inline_keyboard for b in row]
    assert any("codex-choice" in b.text for b in buttons)
