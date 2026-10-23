import asyncio
from unittest.mock import AsyncMock
from test_audit_router import session
from tgbridge import codex_engine


def test_submit_returns_during_turn_and_preserves_fifo(session, monkeypatch):
    async def scenario():
        release = asyncio.Event()
        calls = []
        async def run(route, prompt, cwd):
            calls.append(prompt)
            if len(calls) == 1:
                await release.wait()
            return "answer", {}
        monkeypatch.setattr(codex_engine, "run_turn", run)
        monkeypatch.setattr(codex_engine, "thread_for", lambda key: None)
        session.engine = "codex"
        await asyncio.wait_for(session.submit("first"), .2)
        assert session.busy
        await session.submit("second")
        assert len(calls) == 1 and len(session.pending) == 1
        release.set()
        await asyncio.gather(*list(session._input_tasks))
        assert len(calls) == 2 and "second" in calls[-1]
        assert not session.busy and not session.pending
    asyncio.run(scenario())


def test_stop_cancels_owned_input_without_fallback(session, monkeypatch):
    async def scenario():
        async def run(*args):
            await asyncio.Event().wait()
        monkeypatch.setattr(codex_engine, "run_turn", run)
        monkeypatch.setattr(codex_engine, "thread_for", lambda key: None)
        session.engine = "codex"
        session._send_turn = AsyncMock()
        await session.submit("work")
        tasks = list(session._input_tasks)
        await session.stop()
        assert all(t.done() for t in tasks)
        assert not session.busy and not session._input_tasks
        session._send_turn.assert_not_called()
    asyncio.run(scenario())
