"""Reliability phase: scheduled-Codex interrupt must not kill the scheduler.

Models the reviewer's route_cancel repro but asserts the FIXED behavior.
Synthetic only — no real subprocess is started.
"""
import asyncio
import tempfile
import time
from collections import deque
from types import SimpleNamespace
from unittest.mock import AsyncMock, patch


from tgbridge import codex_engine
from tgbridge.session import AgentConfig, AgentSession, TurnSource
from tgbridge.scheduler import Scheduler


def _run(coro):
    return asyncio.run(coro)


def _make_session(workdir):
    manager = SimpleNamespace(bot=object(), session_ids={}, active="synthetic",
                              agents={})
    s = AgentSession(manager, AgentConfig(name="synthetic", workdir=workdir),
                     "synthetic-cancel@p", 1, 1, None)
    s.engine = "codex"
    s.outbox = SimpleNamespace(emit=lambda *a, **k: None, start=lambda: None,
                               keyboard=lambda *a, **k: None, file=lambda *a: None)
    s._handoff_prompt = lambda eng, text, **kw: text
    s.handoff = SimpleNamespace(state={}, save=lambda: None)
    return s


async def _interrupt_scenario():
    with tempfile.TemporaryDirectory() as d:
        s = _make_session(d)
        scheduler = object.__new__(Scheduler)
        scheduler.mgr = SimpleNamespace(session_for_job=AsyncMock(return_value=s))
        scheduler.jobs = [dict(id="synthetic", agent="synthetic", kind="prompt",
                               text="synthetic job", recur=None,
                               next_ts=time.time() - 1)]
        scheduler._ingest_webhook_inbox = lambda: None
        scheduler._save = lambda: None

        entered = asyncio.Event()
        never = asyncio.Event()

        async def communicate():
            entered.set()
            await never.wait()

        proc = SimpleNamespace(communicate=communicate)
        real_sleep = asyncio.sleep

        async def tick_sleep(delay):
            await real_sleep(0 if delay == 20 else delay)

        with patch("tgbridge.codex_engine.asyncio.create_subprocess_exec",
                   AsyncMock(return_value=proc)), \
             patch.object(codex_engine, "_argv", return_value=["synthetic-only"]), \
             patch.object(codex_engine, "_kill_tree", AsyncMock()), \
             patch("tgbridge.scheduler.asyncio.sleep", tick_sleep):
            loop_task = asyncio.create_task(scheduler._loop())
            try:
                await asyncio.wait_for(entered.wait(), 2)

                # The registered task is the CHILD subprocess-wait, never the
                # scheduler loop task.
                assert codex_engine._running[s.skey] is not loop_task

                stopped = await codex_engine.interrupt(s.skey)
                assert stopped is True

                # Give the loop a couple of ticks; it must stay alive and keep
                # running (the interrupted job is no longer in flight).
                await real_sleep(0)
                await real_sleep(0)
                assert not loop_task.done()
                assert s.busy is False
            finally:
                loop_task.cancel()
                try:
                    await loop_task
                except asyncio.CancelledError:
                    pass


def test_interrupting_scheduled_codex_keeps_scheduler_alive():
    _run(_interrupt_scenario())


# ---- #3: a rejected scheduled turn must not strand a queued human message ----

def test_drain_continues_past_a_rejected_scheduled_turn():
    async def scenario():
        with tempfile.TemporaryDirectory() as d:
            s = _make_session(d)
            s.persist_pending = lambda: None
            calls = []

            async def fake_feed(text, source, echo=False):
                calls.append((text, source.kind))
                if source.kind != "user":
                    return False          # rate-limited: rejected, busy stays False
                s.busy = True             # a real user turn reserves the agent
                return True

            s.feed = fake_feed
            s.pending = deque([("sched-text", TurnSource(kind="sched")),
                               ("human-text", TurnSource(kind="user"))])
            await s._drain()
            # Both were tried, in order, and the human turn actually started.
            assert calls == [("sched-text", "sched"), ("human-text", "user")]
            assert s.busy is True
            assert len(s.pending) == 0
    asyncio.run(scenario())


# ---- #5: each long Codex prompt gets its own file, cleaned up after the turn --

def test_write_prompt_file_is_unique_and_private(tmp_path):
    long = "y" * (codex_engine._MAX_PROMPT_CHARS + 1)
    a = codex_engine._write_prompt_file(long, str(tmp_path))
    b = codex_engine._write_prompt_file(long, str(tmp_path))
    assert a is not None and b is not None and a != b
    assert a.exists() and b.exists()
    assert a.read_text() == long
    assert (a.stat().st_mode & 0o777) == 0o600
    a.unlink()
    b.unlink()
    assert codex_engine._write_prompt_file("short", str(tmp_path)) is None


def test_codex_long_prompt_file_removed_after_turn(tmp_path):
    async def scenario():
        long = "x" * (codex_engine._MAX_PROMPT_CHARS + 10)
        events = ('{"type":"item.completed","item":'
                  '{"type":"agent_message","text":"ok"}}\n')

        async def communicate():
            return (events.encode(), b"")

        proc = SimpleNamespace(communicate=communicate, returncode=0, pid=0)
        with patch("tgbridge.codex_engine.asyncio.create_subprocess_exec",
                   AsyncMock(return_value=proc)), \
             patch.object(codex_engine, "default_model", lambda: None), \
             patch.object(codex_engine, "thread_for", lambda a: None):
            reply, _ = await codex_engine.run_turn("f5-fixture", long, str(tmp_path))
        assert reply == "ok"
        assert list(tmp_path.glob(".codex_prompt.*.txt")) == []
    asyncio.run(scenario())


# ---- #4: a queued reply keeps its own destination topic on retarget ----------

def test_outbox_reply_keeps_its_topic_when_thread_is_retargeted():
    async def scenario():
        from tgbridge.outbox import Outbox
        sent = []

        async def send_message(chat_id, text, **kw):
            sent.append((kw.get("message_thread_id"), text))
            return SimpleNamespace(message_id=len(sent))

        bot = SimpleNamespace(send_message=send_message)
        ob = Outbox(bot, chat_id=1, thread_id=11)
        # Queue a reply for topic 11, then a reply for topic 22 after a retarget
        # (what _attach_thread does when the same session is reused elsewhere).
        ob.emit("reply for topic 11")
        ob.thread_id = 22
        ob.emit("reply for topic 22")
        ob.start()
        # Enough for both batch-window flushes plus the per-send throttle gap.
        await asyncio.sleep(1.3)
        await ob.stop()

        by_topic = {tid: text for tid, text in sent}
        assert by_topic.get(11) == "reply for topic 11"
        assert by_topic.get(22) == "reply for topic 22"
    asyncio.run(scenario())


# ---- #1 (review fix): an interrupt during shutdown must not drain/lose a msg --

def _codex_interrupt_feed_session(d, stopping):
    s = _make_session(d)
    s.persist_pending = lambda: None
    s._stopping = stopping
    s.pending = deque([("queued human", TurnSource(kind="user"))])
    return s


def test_codex_interrupt_while_stopping_keeps_queue():
    async def scenario():
        with tempfile.TemporaryDirectory() as d:
            s = _codex_interrupt_feed_session(d, stopping=True)
            with patch.object(codex_engine, "thread_for", lambda a: None), \
                 patch.object(codex_engine, "run_turn",
                              AsyncMock(side_effect=codex_engine.CodexInterrupted())):
                await s.feed("in-flight turn", TurnSource(kind="user"))
            # Shutdown in progress: the queued human message is NOT popped here
            # (so a plain stop() can't lose it), and no drain was triggered.
            assert [t for t, _ in s.pending] == ["queued human"]
            assert s.busy is False
    asyncio.run(scenario())


def test_codex_interrupt_when_not_stopping_drains_queue():
    async def scenario():
        with tempfile.TemporaryDirectory() as d:
            s = _codex_interrupt_feed_session(d, stopping=False)
            with patch.object(codex_engine, "thread_for", lambda a: None), \
                 patch.object(codex_engine, "run_turn",
                              AsyncMock(side_effect=codex_engine.CodexInterrupted())):
                await s.feed("in-flight turn", TurnSource(kind="user"))
            # Not stopping: a redirect queued after /interrupt is drained.
            assert list(s.pending) == []
            assert s.busy is False
    asyncio.run(scenario())
