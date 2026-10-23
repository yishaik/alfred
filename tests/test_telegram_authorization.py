import asyncio
from types import SimpleNamespace
from unittest.mock import AsyncMock
import pytest
from telegram.ext import ApplicationHandlerStop
from tgbridge import config, main


@pytest.mark.parametrize("chat,user,bot,expected", [(42,42,False,True),(-100,42,False,True),(-100,7,False,True),(-100,8,False,False),(123,42,False,False),(-100,42,True,False),(-100,None,False,False)])
def test_authorization_checks_actor_and_chat(monkeypatch, chat, user, bot, expected):
    monkeypatch.setattr(config, "CHAT_ID", 42)
    monkeypatch.setattr(config, "GROUP_ID", -100)
    monkeypatch.setenv("BRIDGE_ALLOWED_USER_IDS", "7,invalid,-1")
    update = SimpleNamespace(effective_chat=SimpleNamespace(id=chat), effective_user=SimpleNamespace(id=user,is_bot=bot))
    assert config.authorized_update(update) is expected


def test_gate_stops_unauthorized_callback_before_dispatch(monkeypatch):
    query = SimpleNamespace(answer=AsyncMock())
    monkeypatch.setattr(main, "authorized_update", lambda u: False)
    with pytest.raises(ApplicationHandlerStop):
        asyncio.run(main._authorize_update(SimpleNamespace(callback_query=query), None))
    query.answer.assert_awaited_once()
