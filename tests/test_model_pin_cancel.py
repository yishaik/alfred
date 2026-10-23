import asyncio
from types import SimpleNamespace
from unittest.mock import AsyncMock
from tgbridge import handlers, model_catalog


def test_model_command_cancels_paid_pin_before_discovery(monkeypatch):
    session = SimpleNamespace(_external_pin="openai/paid-fixture")
    monkeypatch.setattr(handlers, "_session", AsyncMock(return_value=session))
    async def refresh(s):
        assert s._external_pin == ""
    monkeypatch.setattr(model_catalog, "refresh", refresh)
    monkeypatch.setattr(handlers, "_model_picker_text", lambda s: "models")
    monkeypatch.setattr(handlers, "model_kb", lambda s: None)
    message = SimpleNamespace(reply_text=AsyncMock())
    asyncio.run(handlers.cmd_model(SimpleNamespace(message=message), None))
    assert session._external_pin == ""
