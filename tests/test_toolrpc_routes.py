import asyncio
from types import SimpleNamespace
from unittest.mock import AsyncMock
import pytest
from tgbridge import toolrpc


def test_rpc_resolves_exact_route_and_rejects_mismatch():
    async def scenario():
        private = SimpleNamespace(cfg=SimpleNamespace(name="worker"))
        topic = SimpleNamespace(cfg=SimpleNamespace(name="worker"))
        mgr = SimpleNamespace(sessions={"worker@p": private, "worker@t123": topic}, agents={"worker": object()}, active="other", session_for_agent=AsyncMock(return_value=private))
        rpc = object.__new__(toolrpc.ToolRPC)
        rpc.mgr = mgr
        assert await rpc._session_for("worker", "worker@t123") is topic
        assert await rpc._session_for("worker") is private
        for agent, route in [("worker", "missing"), ("other", "worker@t123"), ("unknown", "")]:
            with pytest.raises(ValueError):
                await rpc._session_for(agent, route)
    asyncio.run(scenario())
