import asyncio
from types import SimpleNamespace
from unittest.mock import AsyncMock
from test_audit_router import session
from tgbridge import bridgetools, toolrpc


def rpc_for(session, monkeypatch, name):
    tool = SimpleNamespace(name=name, handler=AsyncMock(return_value={"ok": True}))
    monkeypatch.setattr(bridgetools, "build_tools", lambda s: [tool])
    rpc = object.__new__(toolrpc.ToolRPC)
    rpc._session_for = AsyncMock(return_value=session)
    return rpc, tool


def test_rpc_readonly_denies_write_even_when_autoapproved(session, monkeypatch):
    session.cfg.read_only = True
    session.cfg.auto_approve = True
    rpc, tool = rpc_for(session, monkeypatch, "remember")
    assert "error" in asyncio.run(rpc._call("audit", "remember", {}))
    tool.handler.assert_not_awaited()


def test_rpc_readonly_allows_known_read(session, monkeypatch):
    session.cfg.read_only = True
    session.cfg.auto_approve = False
    rpc, tool = rpc_for(session, monkeypatch, "recall")
    assert "result" in asyncio.run(rpc._call("audit", "recall", {}))
    tool.handler.assert_awaited_once()


def test_rpc_write_requires_real_user_permission(session, monkeypatch):
    async def scenario():
        session.cfg.auto_approve = False
        rpc, tool = rpc_for(session, monkeypatch, "remember")
        session.outbox.keyboard = lambda *a: None
        task = asyncio.create_task(rpc._call("audit", "remember", {"text": "test"}))
        await asyncio.sleep(0)
        tool.handler.assert_not_awaited()
        assert len(session.perms) == 1
        session.resolve_perm(next(iter(session.perms)), "d")
        assert "error" in await task
        tool.handler.assert_not_awaited()
    asyncio.run(scenario())
