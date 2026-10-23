import asyncio
from types import SimpleNamespace
from unittest.mock import AsyncMock
from test_audit_router import session
from tgbridge import codex_engine, engine_ui


def test_readonly_cannot_select_unrestricted_engine(session, monkeypatch):
    session.cfg.read_only = True
    monkeypatch.setattr(engine_ui, "readiness", lambda e: (True, ""))
    assert not engine_ui.select(session, "codex")[0]
    assert not engine_ui.select(session, "antigravity")[0]
    assert session.engine == "claude"


def test_readonly_stored_engine_and_failover_fail_closed(session, monkeypatch):
    session.cfg.read_only = True
    session.engine = "codex"
    session._turn_input_text = "verify"
    run = AsyncMock()
    monkeypatch.setattr(codex_engine, "run_turn", run)
    monkeypatch.setattr(codex_engine, "available", lambda: (True, "fixture"))
    session._maybe_route = AsyncMock(side_effect=lambda text: text)
    session._send_turn = AsyncMock()
    async def scenario():
        assert not await session._failover_to_codex()
        assert await session.feed("verify")
    asyncio.run(scenario())
    run.assert_not_called()
    session._send_turn.assert_awaited_once()
    assert session.engine == "claude"


def test_readonly_sdk_options_never_bypass_policy(session):
    session.cfg.read_only = True
    session.cfg.auto_approve = True
    session.mgr.memory_for = lambda name: SimpleNamespace(render_prompt=lambda: "")
    options = session._options()
    assert options.permission_mode != "bypassPermissions"
    assert "Task" not in options.allowed_tools and "Bash" not in options.allowed_tools
    assert "Bash" in options.disallowed_tools and "Write" in options.disallowed_tools
    assert "mcp__bridge__remember" not in options.allowed_tools
    assert "mcp__bridge__recall" in options.allowed_tools
