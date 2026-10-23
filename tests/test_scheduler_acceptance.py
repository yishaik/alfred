import asyncio
import time
from types import SimpleNamespace
from unittest.mock import AsyncMock
import pytest
from tgbridge.scheduler import Scheduler


@pytest.mark.parametrize("recur", [None, "daily 09:00"])
def test_rejected_scheduled_turn_is_retried_not_acknowledged(recur):
    async def scenario():
        session = SimpleNamespace(outbox=SimpleNamespace(emit=lambda *a: None), feed=AsyncMock(return_value=False))
        scheduler = object.__new__(Scheduler)
        scheduler.mgr = SimpleNamespace(session_for_job=AsyncMock(return_value=session))
        job = dict(id="test", agent="main", kind="prompt", text="work", recur=recur)
        scheduler.jobs = [job]
        await scheduler._fire(job)
        assert scheduler.jobs == [job] and job["fails"] == 1
        assert job["next_ts"] > time.time() + 55
        if recur is None:
            session.feed.return_value = True
            await scheduler._fire(job)
            assert not scheduler.jobs
    asyncio.run(scenario())
