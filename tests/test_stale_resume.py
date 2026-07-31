"""A resume id the CLI doesn't know must be dropped, not retried forever.

state/ is portable but ~/.claude isn't: restore a state backup on another
machine and sessions.json points at a transcript that no longer exists. The CLI
then exits 1 on every start with "No conversation found with session ID", which
used to surface as an unexplained "Command failed with exit code 1".
"""

import asyncio
from collections import deque

from tgbridge.session import AgentSession


class FakeOutbox:
    def __init__(self):
        self.msgs = []

    def start(self):
        pass

    def emit(self, text):
        self.msgs.append(text)


class FakeMgr:
    def __init__(self):
        self.saved = []

    def save_session_id(self, skey, sid):
        self.saved.append((skey, sid))


def make_session(stderr_lines, fail_times):
    """A bare AgentSession with connect() stubbed — no SDK, no subprocess."""
    s = AgentSession.__new__(AgentSession)
    s.skey = "main@p"
    s.session_id = "dead-beef"
    s.connected = False
    s.busy = True
    s.stderr_tail = deque(maxlen=40)
    s.outbox = FakeOutbox()
    s.mgr = FakeMgr()
    s._stopping = False
    s._typing = None
    s._watchdog = None
    s.attempts = 0

    async def noop():
        return None

    s._consume = noop
    s._typing_loop = noop
    s._watchdog_loop = noop

    async def fake_connect(fork):
        s.stderr_tail.clear()
        s.attempts += 1
        if s.attempts <= fail_times:
            for line in stderr_lines:
                s.stderr_tail.append(line)
            raise RuntimeError("Command failed with exit code 1")
        return object()

    s._connect = fake_connect
    s._ensure_workdir = lambda: None
    return s


def start(s):
    """Run _start_locked and report the exception it raised, if any."""
    async def run():
        try:
            await AgentSession._start_locked(s, resume=True)
        except Exception as e:
            return e
        return None
    return asyncio.run(run())


def test_stale_resume_id_is_dropped_and_retried():
    s = make_session(["No conversation found with session ID: dead-beef"], 1)
    err = start(s)
    assert err is None or not isinstance(err, RuntimeError)
    assert s.attempts == 2                      # retried once, without resume
    assert s.session_id is None
    assert s.mgr.saved == [("main@p", None)]
    assert "fresh session" in s.outbox.msgs[0]


def test_other_failures_still_propagate():
    """A real crash must NOT silently discard the conversation."""
    s = make_session(["ENOSPC: no space left on device"], 1)
    err = start(s)
    assert isinstance(err, RuntimeError)
    assert s.attempts == 1                      # no blind retry
    assert s.session_id == "dead-beef"          # conversation preserved
    assert s.mgr.saved == []
