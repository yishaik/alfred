"""Offline regression tests: no Telegram delivery and no inference requests."""
import asyncio
import json
from collections import deque
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest

from tgbridge import codex_engine, handoff, model_catalog
from tgbridge.session import AgentConfig, AgentSession


@pytest.fixture
def session(tmp_path, monkeypatch):
    monkeypatch.setattr(handoff, "STATE_DIR", tmp_path)
    cfg = AgentConfig(name="test-handoff", workdir=str(tmp_path))
    mgr = SimpleNamespace(bot=object(), session_ids={}, active=cfg.name, agents={cfg.name: cfg})
    s = AgentSession(mgr, cfg, "test-handoff@p", 1, 1, None)
    s.outbox = SimpleNamespace(emit=lambda *a: None, keyboard=lambda *a, **k: None,
                               start=lambda: None, muted=False)
    s._react = lambda *a: None
    s._maybe_route = AsyncMock(side_effect=lambda text: text)
    s.client = SimpleNamespace(query=AsyncMock(), set_model=AsyncMock())
    s.connected = True
    return s


def test_model_switch_keeps_native_thread(tmp_path, monkeypatch):
    monkeypatch.setattr(codex_engine, "_threads", {"route": "thread-one"})
    monkeypatch.setattr(codex_engine, "_models", {})
    monkeypatch.setattr(codex_engine, "_STATE_FILE", tmp_path / "native.json")
    codex_engine.set_model("route", "new-model")
    assert codex_engine.thread_for("route") == "thread-one"
    argv = codex_engine._argv("continue", ".", "thread-one", "new-model")
    assert argv[1:3] == ["exec", "resume"]
    assert argv[argv.index("-m") + 1] == "new-model"
    assert "--cd" not in argv
    saved = json.loads((tmp_path / "native.json").read_text())
    assert saved["threads"]["route"] == "thread-one"
    assert saved["models"]["route"] == "new-model"


def test_handoff_round_trip_and_restart(tmp_path):
    c = handoff.Conversation("one", tmp_path)
    detail = "x" * 1500 + " keep the green button"
    c.record("claude", "implement this", detail)
    assert detail in c.prompt("codex", "continue")
    c.record("codex", "continue", "green button implemented")
    back = c.prompt("claude", "test it")
    assert "green button implemented" in back
    assert detail not in back  # Claude already saw its own turn.
    c.state["engine"] = "codex"
    c.save()
    restored = handoff.Conversation("one", tmp_path)
    assert restored.state["engine"] == "codex"
    assert restored.prompt("codex", "next") == "next"
    assert "green button implemented" in restored.prompt("codex", "next", fresh=True)
    assert handoff.Conversation("different-topic", tmp_path).prompt("codex", "next") == "next"


def test_failed_turn_does_not_consume_context(tmp_path):
    c = handoff.Conversation("one", tmp_path)
    c.record("claude", "task", "plan")
    first = c.prompt("codex", "continue")
    assert c.prompt("codex", "continue") == first
    assert c.state["seen"] == {"claude": 1}


def test_long_exchange_keeps_tail_and_complete_journal(tmp_path):
    c = handoff.Conversation("one", tmp_path)
    answer = "start-" + "x" * 40000 + "-final decision"
    c.record("claude", "task", answer)
    prompt = c.prompt("codex", "go", budget=1000)
    assert "shortened" in prompt and "final decision" in prompt
    assert len(prompt) < 1600
    assert json.loads(c.journal.read_text())["answer"] == answer


def test_history_omission_is_explicit(tmp_path):
    c = handoff.Conversation("one", tmp_path)
    for i in range(70):
        c.record("claude", str(i), "ok")
    assert "Earlier exchanges omitted: 6" in c.prompt("codex", "go")
    assert len(c.journal.read_text().splitlines()) == 70


def test_model_catalog_filters_and_labels():
    assert model_catalog.rows([
        {"id": "a", "displayName": "A"}, {"id": "hidden", "hidden": True},
        {"value": "sonnet", "displayName": "Sonnet"}, {"id": "a"},
        {"id": "x" * 65}, None]) == [("a", "A"), ("sonnet", "Sonnet")]


def test_codex_catalog_reads_visibility_and_labels(tmp_path, monkeypatch):
    monkeypatch.setenv("CODEX_HOME", str(tmp_path))
    (tmp_path / "models_cache.json").write_text(json.dumps({"models": [
        {"slug": "new", "display_name": "New model", "visibility": "list"},
        {"slug": "private", "visibility": "hide"}, {"slug": "new"}]}))
    assert codex_engine.list_models() == [("new", "New model")]


def test_external_turns_are_serial_and_route_isolated(session, monkeypatch):
    async def scenario():
        started, release = asyncio.Event(), asyncio.Event()
        calls = []
        async def run(route, prompt, cwd):
            calls.append((route, prompt))
            if len(calls) == 1:
                started.set()
                await release.wait()
            return "answer " + str(len(calls)), {}
        monkeypatch.setattr(codex_engine, "run_turn", run)
        monkeypatch.setattr(codex_engine, "thread_for", lambda key: None)
        session.engine = "codex"
        task = asyncio.create_task(session.feed("first"))
        await started.wait()
        assert session.busy
        await session.feed("second")
        assert len(calls) == 1 and len(session.pending) == 1
        release.set()
        await task
        assert len(calls) == 2 and all(r == session.skey for r, p in calls)
        assert "first" in calls[1][1] and "answer 1" in calls[1][1]
        assert not session.busy and not session.pending
    asyncio.run(scenario())


def test_external_to_claude_passes_context(session, monkeypatch):
    session.handoff.record("codex", "change the control", "green button done")
    asyncio.run(session.feed("now test"))
    sent = session.client.query.await_args.args[0]
    assert "green button done" in sent and "now test" in sent
    assert session.busy  # SDK ResultMessage releases the turn, not query().


def test_external_error_falls_through_without_consuming_handoff(session, monkeypatch):
    session.engine = "codex"
    session.handoff.record("antigravity", "task", "decision")
    monkeypatch.setattr(codex_engine, "run_turn", AsyncMock(side_effect=RuntimeError("offline")))
    monkeypatch.setattr(codex_engine, "thread_for", lambda key: None)
    asyncio.run(session.feed("continue"))
    assert "decision" in session.client.query.await_args.args[0]
    assert "codex" not in session.handoff.state["seen"]
    session._maybe_route.assert_not_awaited()


def test_capture_external_answer(session, monkeypatch):
    session.engine = "codex"
    monkeypatch.setattr(codex_engine, "run_turn", AsyncMock(return_value=("captured", {})))
    monkeypatch.setattr(codex_engine, "thread_for", lambda key: None)
    assert asyncio.run(session.collect("task", timeout=1)) == "captured"
    assert not session.busy


def test_queue_full_does_not_evict(session):
    session.busy = True
    session.pending = deque([("kept", None)], maxlen=1)
    assert asyncio.run(session.feed("overflow")) is False
    assert session.pending[0][0] == "kept"


def test_discovery_failure_retains_catalog(session, monkeypatch):
    session.engine = "codex"
    session.model_catalog = {"codex": [("known", "Known") ]}
    monkeypatch.setattr(model_catalog, "codex_models", AsyncMock(side_effect=RuntimeError("offline")))
    asyncio.run(model_catalog.refresh(session))
    assert session.model_catalog["codex"] == [("known", "Known")]


def test_claude_completion_persists_before_capture_return(session):
    from claude_agent_sdk import ResultMessage
    async def scenario():
        session.busy = True
        session._turn_input_text = "task"
        session._turn_final_text = "complete decision"
        session._capture = {"future": asyncio.get_running_loop().create_future(),
                            "texts": ["complete decision"]}
        session.mgr.add_cost = lambda cost: (0, None)
        await session._handle(ResultMessage("success", 1, 1, False, 1, "s"))
        assert session._capture["future"].result() == "complete decision"
        assert not session.busy
        assert "complete decision" in session.handoff.prompt("codex", "go")
    asyncio.run(scenario())


def test_engine_change_is_refused_while_busy(session, monkeypatch):
    from tgbridge import handlers
    session.busy = True
    monkeypatch.setattr(handlers, "_session", AsyncMock(return_value=session))
    update = SimpleNamespace(message=SimpleNamespace(reply_text=AsyncMock()))
    asyncio.run(handlers.cmd_engine(update, SimpleNamespace(args=["codex"])))
    assert session.engine == "claude"
    update.message.reply_text.assert_awaited_once()


def test_bootstrap_existing_claude_transcript(tmp_path, monkeypatch):
    import claude_agent_sdk
    from pathlib import Path
    monkeypatch.setattr(Path, "home", lambda: tmp_path)
    monkeypatch.setattr(claude_agent_sdk, "project_key_for_directory", lambda _: "project")
    folder = tmp_path / ".claude/projects/project"
    folder.mkdir(parents=True)
    rows = [{"type": who, "message": {"content": text}} for who, text in [
        ("user", "original request"), ("assistant", "working"),
        ("assistant", "final decision")]]
    (folder / "old.jsonl").write_text("\n".join(json.dumps(r) for r in rows))
    c = handoff.Conversation("one", tmp_path / "state")
    c.seed_claude("anything", "old")
    prompt = c.prompt("codex", "continue")
    assert "original request" in prompt and "final decision" in prompt
    assert c.seq == 1
    c.seed_claude("anything", "old")
    assert c.seq == 1
