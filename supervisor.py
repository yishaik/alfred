#!/usr/bin/env python3
"""Supervisor: run bridge.py forever with crash-loop backoff.

start_bridge.bat delegates here because batch can't do time math and
PowerShell may be unavailable (it broke when C: filled up once). A bridge
that exits within FAST_EXIT_SECS gets an escalating restart delay
(5s -> 300s over six rungs); a healthy long run resets the ladder. The early
rungs are deliberately short: the common fast exit is a transient network
failure (DNS, flaky uplink) that clears in seconds, and the old 5/60/300 ladder
put Alfred offline for five minutes on the second one. Only a real crash loop
— five consecutive fast exits — reaches the slow rungs. All bridge output
goes to bridge.log, rotated at ~10MB. No third-party imports — this must run
even when the venv is broken.
"""

import os
import signal
import subprocess
import sys
import time

ROOT = os.path.dirname(os.path.abspath(__file__))
LOG = os.path.join(ROOT, "bridge.log")
FAST_EXIT_SECS = 30
DELAYS = [5, 5, 15, 30, 60, 300]
MAX_LOG_BYTES = 10 * 1024 * 1024
# the bridge exits with this when another instance holds the bot token (see
# config.CONFLICT_EXIT_RC). Restarting fast would just resume the tug-of-war,
# so wait long enough for a human to stop the duplicate.
CONFLICT_RC = 75
CONFLICT_DELAY = 900


def _rotate():
    try:
        if os.path.exists(LOG) and os.path.getsize(LOG) > MAX_LOG_BYTES:
            old = LOG + ".old"
            if os.path.exists(old):
                os.remove(old)
            os.replace(LOG, old)
    except OSError:
        pass


def _note(fh, text):
    fh.write(f"[supervisor {time.strftime('%Y-%m-%d %H:%M:%S')}] {text}\n")
    fh.flush()


def main():
    os.environ["PYTHONUTF8"] = "1"
    # SIGTERM (a clean service/manager stop) must end the supervisor loop, not
    # trigger a respawn. Raising KeyboardInterrupt reuses the Ctrl-C path below,
    # which terminates the running bridge child and returns.
    def _on_sigterm(_signum, _frame):
        raise KeyboardInterrupt
    try:
        signal.signal(signal.SIGTERM, _on_sigterm)
    except (ValueError, OSError):
        pass                # non-main thread / platform without SIGTERM
    fast_exits = 0
    while True:
        _rotate()
        with open(LOG, "a", encoding="utf-8", errors="replace") as fh:
            _note(fh, "launching bridge")
            start = time.monotonic()
            try:
                proc = subprocess.Popen(
                    [sys.executable, "-u", os.path.join(ROOT, "bridge.py")],
                    stdout=fh, stderr=subprocess.STDOUT, cwd=ROOT)
                rc = proc.wait()
            except KeyboardInterrupt:
                try:
                    proc.terminate()
                except Exception:
                    pass
                _note(fh, "supervisor stopped (Ctrl-C)")
                return
            uptime = time.monotonic() - start
            fast_exits = fast_exits + 1 if uptime < FAST_EXIT_SECS else 0
            delay = DELAYS[min(fast_exits, len(DELAYS) - 1)]
            why = f" (fast-exit #{fast_exits})" if fast_exits else ""
            if rc == CONFLICT_RC:
                fast_exits = 0        # not a crash loop — don't poison the ladder
                delay = CONFLICT_DELAY
                why = " (another instance holds the bot token)"
            _note(fh, f"bridge exited rc={rc} after {uptime:.0f}s — "
                      f"restarting in {delay}s{why}")
        time.sleep(delay)


if __name__ == "__main__":
    main()
