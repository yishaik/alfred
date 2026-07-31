"""In-process event counters surfaced in /status and the daily health report.

Purpose:  In-process counters — "what went wrong since the last restart" for /status.
Inputs:   bump(name) calls from anywhere in the bridge.
Outputs:  A counter mapping for /status and the daily health report.
Key fns:  bump, summary.
Deps:     none.
Note:     Resets on restart by design — logs and audit.jsonl hold the history.
Updated:  2026-07-31

Counters reset when the bridge restarts — they answer "what went wrong since
the last restart", not "ever"; the logs and audit trail hold history.
"""

from collections import Counter

counters: Counter = Counter()


def bump(name: str, n: int = 1) -> None:
    counters[name] += n


def summary() -> str:
    """One compact line, or "" when nothing has been counted."""
    if not counters:
        return ""
    return " · ".join(f"{k}:{v}" for k, v in sorted(counters.items()))
