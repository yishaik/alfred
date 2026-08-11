"""A question belongs to the session that asked it.

On 2026-08-11 buttons from questions asked days earlier suddenly appeared after
a /clear, and their answers were fed into a brand-new session as user turns. Two
faults combined: _ask_question waited on _q_lock without a bound (so every
question asked after an unanswered one parked behind it indefinitely), and the
ask tasks were untracked fire-and-forget, so a restart could not cancel them.
"""

import asyncio

import pytest

from tgbridge.session import AgentSession


class _Outbox:
    """Records what the session would have sent, and never touches Telegram."""

    def __init__(self):
        self.texts: list[str] = []
        self.keyboards: list[str] = []
        self.muted = False

    def emit(self, text):
        self.texts.append(text)

    def keyboard(self, text, markup, on_sent=None):
        self.keyboards.append(text)

    def start(self):
        pass


def _session() -> AgentSession:
    """A Session with just enough wired up to exercise the question path."""
    s = AgentSession.__new__(AgentSession)
    s.sid = "t"
    s.outbox = _Outbox()
    s.questions = {}
    s.qcounter = 0
    s._q_lock = asyncio.Lock()
    s._q_tasks = set()
    s.generation = 1
    s.fed: list[str] = []

    async def feed(text, *a, **k):
        s.fed.append(text)
        return True

    s.feed = feed
    return s


def _q(text, *labels):
    return {"questions": [{"question": text,
                           "options": [{"label": l} for l in labels]}]}


def test_second_question_is_dropped_not_parked():
    """The old code queued on _q_lock; days of questions piled up invisibly."""
    async def scenario():
        s = _session()
        s._spawn_question(_q("first?", "a", "b"))
        await asyncio.sleep(0)          # let the first one take the lock
        second = await asyncio.wait_for(
            s._ask_question(_q("second?", "c")), timeout=2)
        assert second is None
        assert len(s.questions) == 1    # only the first is on screen
        assert any("already waiting" in t for t in s.outbox.texts)
        assert s.fed == []
        for t in s._q_tasks:
            t.cancel()

    asyncio.run(scenario())


def test_restart_cancels_pending_questions_and_feeds_nothing():
    """_stop_locked must leave nothing behind that can answer into the next
    session — this is the bug the user hit."""
    async def scenario():
        s = _session()
        s._spawn_question(_q("stale?", "a"))
        await asyncio.sleep(0)
        assert s.questions and s._q_tasks

        for t in list(s._q_tasks):
            t.cancel()
        s._q_tasks.clear()
        for st in list(s.questions.values()):
            if not st["future"].done():
                st["future"].set_result(None)
        s.questions.clear()
        await asyncio.sleep(0)

        assert s.fed == []              # nothing fed into the new session
        assert not s._q_lock.locked()   # and the lock is free again

    asyncio.run(scenario())


def test_answer_from_a_previous_generation_is_discarded():
    """A tap that lands after a restart must not be fed as this session's turn."""
    async def scenario():
        s = _session()
        task = asyncio.ensure_future(s._ask_question(_q("q?", "yes", "no")))
        await asyncio.sleep(0)
        qid, st = next(iter(s.questions.items()))
        s.generation += 1               # the session restarted underneath it
        st["future"].set_result("yes")
        assert await asyncio.wait_for(task, timeout=2) is None

    asyncio.run(scenario())


def test_answer_in_the_same_generation_is_returned():
    """The normal path still works: one question, one tap, one answer."""
    async def scenario():
        s = _session()
        task = asyncio.ensure_future(s._ask_question(_q("q?", "yes", "no")))
        await asyncio.sleep(0)
        st = next(iter(s.questions.values()))
        st["future"].set_result("yes")
        assert await asyncio.wait_for(task, timeout=2) == "q? -> yes"
        assert not s._q_lock.locked()

    asyncio.run(scenario())


def test_expired_question_returns_none_and_says_so(monkeypatch):
    """An unanswered question must not hold the lock forever."""
    async def scenario():
        import tgbridge.session as sess
        monkeypatch.setattr(sess, "QUESTION_TIMEOUT", 0.01)
        s = _session()
        assert await asyncio.wait_for(
            s._ask_question(_q("forever?", "a")), timeout=2) is None
        assert any("expired" in t for t in s.outbox.texts)
        assert not s._q_lock.locked()
        assert s.questions == {}

    asyncio.run(scenario())
