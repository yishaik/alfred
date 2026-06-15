"""In-process event counters surfaced in /status and the daily health report.

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
