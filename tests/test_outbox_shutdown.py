"""The outbox sender must not spin when its event loop is gone.

On 2026-07-31 a single Ctrl-C put _run into a hot loop: the loop was closed,
every iteration raised RuntimeError, the handler logged and continued, and 873
identical tracebacks hit bridge.log in under a second.
"""

import asyncio
import logging

import pytest

from tgbridge.outbox import Outbox


class FakeBot:
    async def send_message(self, *a, **k):
        raise AssertionError("nothing should be sent in these tests")


def _outbox():
    return Outbox(FakeBot(), chat_id=1)


@pytest.mark.parametrize("msg", ["Event loop is closed", "no running event loop"])
def test_dead_loop_stops_the_sender(msg, caplog):
    """The sender returns instead of looping, and says so exactly once."""
    async def scenario():
        ob = _outbox()

        async def boom():
            raise RuntimeError(msg)

        ob.queue.get = boom
        # completes rather than hanging => the loop exited on its own
        await asyncio.wait_for(ob._run(), timeout=2)

    with caplog.at_level(logging.WARNING):
        asyncio.run(scenario())
    assert sum("sender stopping" in r.message for r in caplog.records) == 1


def test_other_runtime_errors_still_drop_the_item_and_continue():
    """An unrelated RuntimeError is a poisonous item, not a dead loop — delivery
    must survive it (the whole point of the wrapper)."""
    async def scenario():
        ob = _outbox()
        calls = {"n": 0}

        async def flaky():
            calls["n"] += 1
            if calls["n"] < 3:
                raise RuntimeError("something unrelated broke")
            raise asyncio.CancelledError

        ob.queue.get = flaky
        with pytest.raises(asyncio.CancelledError):
            await ob._run()
        return calls["n"]

    assert asyncio.run(scenario()) == 3


def test_repeating_failure_backs_off_instead_of_hot_looping():
    """A failure on every iteration must slow down, not pin a core."""
    async def scenario():
        ob = _outbox()
        slept = []

        async def always_fails():
            raise ValueError("poison")

        ob.queue.get = always_fails

        real_sleep = asyncio.sleep

        async def fake_sleep(sec, *a, **k):
            slept.append(sec)
            if len(slept) >= 5:
                raise asyncio.CancelledError
            await real_sleep(0)

        asyncio.sleep = fake_sleep
        try:
            with pytest.raises(asyncio.CancelledError):
                await ob._run()
        finally:
            asyncio.sleep = real_sleep
        return slept

    slept = asyncio.run(scenario())
    assert slept and all(s > 0 for s in slept)
    assert max(slept) <= 10.0
