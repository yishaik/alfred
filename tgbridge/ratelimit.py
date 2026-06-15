"""Rate limiting primitives — the guard rails against infinite loops.

Three loop hazards exist in this bridge and each has a dedicated guard:
  * bot<->bot ping-pong        -> hop counters + per-pair TokenBucket
  * runaway scheduler/agents   -> per-agent non-human turn budget
  * crash-restart storms       -> Backoff with fresh-session fallback
"""

import time
from collections import defaultdict, deque


class TokenBucket:
    """Classic token bucket: `rate` tokens per `per` seconds, burst = capacity."""

    def __init__(self, rate: float, per: float, capacity: float | None = None):
        self.rate = rate / per
        self.capacity = capacity if capacity is not None else rate
        self.tokens = self.capacity
        self.updated = time.monotonic()

    def allow(self, cost: float = 1.0) -> bool:
        now = time.monotonic()
        self.tokens = min(self.capacity, self.tokens + (now - self.updated) * self.rate)
        self.updated = now
        if self.tokens >= cost:
            self.tokens -= cost
            return True
        return False

    def seconds_until(self, cost: float = 1.0) -> float:
        now = time.monotonic()
        tokens = min(self.capacity, self.tokens + (now - self.updated) * self.rate)
        return max(0.0, (cost - tokens) / self.rate)


class PairLimiter:
    """One bucket per key (e.g. (src_agent, dst_agent) message pairs)."""

    def __init__(self, rate: float, per: float):
        self.rate, self.per = rate, per
        self.buckets: dict = defaultdict(lambda: TokenBucket(self.rate, self.per))

    def allow(self, key) -> bool:
        return self.buckets[key].allow()


class Backoff:
    """Crash-restart backoff. Tracks recent failures; suggests delay and when to
    give up on resuming (fall back to a fresh session)."""

    def __init__(self, fresh_after: int = 3, window: float = 120.0, max_delay: float = 60.0):
        self.fresh_after = fresh_after
        self.window = window
        self.max_delay = max_delay
        self.failures: deque[float] = deque(maxlen=16)

    def record(self) -> tuple[float, bool]:
        """Register a failure. Returns (delay_seconds, should_drop_resume)."""
        now = time.monotonic()
        self.failures.append(now)
        recent = [t for t in self.failures if now - t < self.window]
        delay = min(self.max_delay, 2.0 ** len(recent))
        return delay, len(recent) >= self.fresh_after

    def reset(self) -> None:
        self.failures.clear()
