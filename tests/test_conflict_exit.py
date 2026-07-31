"""A sustained getUpdates conflict must concede the token, not spin forever."""

import asyncio

import pytest
from telegram.error import Conflict

import tgbridge.handlers as handlers
from tgbridge.config import CONFLICT_EXIT_RC


class FakeBot:
    def __init__(self):
        self.sent = []

    async def send_message(self, chat_id, text, **kw):
        self.sent.append(text)


class FakeApp:
    def __init__(self):
        self.stopped = False

    def stop_running(self):
        self.stopped = True


class FakeCtx:
    def __init__(self, err):
        self.error = err
        self.bot = FakeBot()
        self.application = FakeApp()


@pytest.fixture(autouse=True)
def reset_state():
    handlers._conflict_since = 0.0
    handlers._conflict_last = 0.0
    handlers._last_err_note = 0.0
    handlers.exit_code = 0
    yield
    handlers.exit_code = 0


def fire(monkeypatch, at):
    """Deliver one Conflict to on_error with the clock pinned at `at`."""
    monkeypatch.setattr(handlers, "_now", lambda: at)
    ctx = FakeCtx(Conflict("terminated by other getUpdates"))
    asyncio.run(handlers.on_error(None, ctx))
    return ctx


def test_brief_conflict_warns_but_keeps_running(monkeypatch):
    ctx = fire(monkeypatch, 1000.0)
    assert not ctx.application.stopped
    assert handlers.exit_code == 0
    assert "Another bridge instance" in ctx.bot.sent[0]


def test_sustained_conflict_exits_with_tempfail(monkeypatch):
    # PTB retries every few seconds, so a real conflict arrives as a stream
    ctx = None
    for t in range(1000, 1000 + 660, 30):
        ctx = fire(monkeypatch, float(t))
        if ctx.application.stopped:
            break
    assert ctx.application.stopped
    assert handlers.exit_code == CONFLICT_EXIT_RC
    assert "Shutting this one down" in ctx.bot.sent[0]


def test_gap_resets_the_window(monkeypatch):
    """A conflict that cleared and came back much later starts a fresh run —
    otherwise two unrelated blips hours apart would trip the exit."""
    fire(monkeypatch, 1000.0)
    ctx = fire(monkeypatch, 1000.0 + 5000)     # gap > CONFLICT_WINDOW_SECS
    assert not ctx.application.stopped
    assert handlers.exit_code == 0
