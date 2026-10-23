import asyncio
import json
from unittest.mock import AsyncMock
import httpx
from test_audit_router import session
from tgbridge import codex_engine, router


def test_codex_one_shot_reaches_real_http_adapter(session, monkeypatch):
    async def scenario():
        seen = []
        def transport(request):
            seen.append(request)
            return httpx.Response(200, json={"choices": [{"message": {"content": "external answer"}}]})
        client = httpx.AsyncClient
        monkeypatch.setattr(httpx, "AsyncClient", lambda **kw: client(transport=httpx.MockTransport(transport), **kw))
        monkeypatch.setenv("OPENROUTER_API_KEY", "fixture")
        monkeypatch.setattr(router, "load_config", lambda: router.RouterConfig())
        monkeypatch.setattr(router, "_load_usage", lambda: {"counts": {}})
        monkeypatch.setattr(router, "_bump_usage", lambda *a: None)
        monkeypatch.setattr(router, "_log_decision", lambda *a, **k: None)
        run = AsyncMock()
        monkeypatch.setattr(codex_engine, "run_turn", run)
        session.engine = "codex"
        session._external_pin = "openai/fixture"
        assert await session.feed("hello")
        assert session.engine == "codex" and not session.busy
        run.assert_not_called()
        assert len(seen) == 1
        assert str(seen[0].url) == "https://openrouter.ai/api/v1/chat/completions"
        assert json.loads(seen[0].content)["model"] == "openai/fixture"
        assert seen[0].headers["authorization"] == "Bearer fixture"
    asyncio.run(scenario())


def test_collect_returns_router_answer_without_hanging(session, monkeypatch):
    monkeypatch.setattr(router, "classify", AsyncMock(return_value=router.Decision(route="free", text="question")))
    monkeypatch.setattr(router, "answer_free", AsyncMock(return_value=("captured answer", "fixture")))
    monkeypatch.setattr(router, "load_config", lambda: router.RouterConfig())
    async def scenario():
        return await asyncio.wait_for(session.collect("question"), .5)
    assert asyncio.run(scenario()) == "captured answer"
    assert session._capture is None and not session.busy and not session.outbox.muted
