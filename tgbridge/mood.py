"""A lightweight mood for an agent — transient emotional weather on top of the
stable character sheet (soul.py).

The soul is *who the agent is* and lives in the system prompt. The mood is *how
the agent feels right now* and is injected per-turn, only when it changes, so a
long or rocky session subtly shifts the tone without ever spamming the chat.

Signals are deliberately cheap (no I/O, no persistence): turns taken, a streak
of errors or wins, session age, and whether we just recovered from a crash. A
fresh session starts neutral, which is exactly right.

Priority order in describe(): recovering > cautious (errors) > weary (long) >
in-the-zone (wins) > neutral.
"""

import time

# Tunables — thresholds at which the weather turns.
LONG_TURNS = 20         # turns in one session before "weary" sets in
LONG_HOURS = 2.0        # …or this many hours of wall-clock
ERROR_STREAK = 2        # consecutive error turns -> "cautious"
WIN_STREAK = 6          # consecutive clean turns -> "in the zone"


class Mood:
    def __init__(self):
        self.turns = 0
        self.errors = 0          # consecutive error turns
        self.wins = 0            # consecutive clean turns
        self.recovered = False   # just came back from an unexpected exit
        self._started = time.monotonic()
        self._last_emitted = ""  # last nudge handed to a turn

    # -- signal intake ------------------------------------------------------- #
    def note_result(self, is_error: bool) -> None:
        self.turns += 1
        if is_error:
            self.errors += 1
            self.wins = 0
        else:
            self.wins += 1
            self.errors = 0
            self.recovered = False   # a clean turn clears the recovery flag

    def note_restart(self, crashed: bool) -> None:
        """Called when the session (re)starts. A crash leaves the agent a touch
        more careful on its next turn; a clean restart resets the weather."""
        if crashed:
            self.recovered = True
        else:
            self.recovered = False
            self.errors = 0

    # -- read-out ------------------------------------------------------------ #
    def _age_hours(self) -> float:
        return (time.monotonic() - self._started) / 3600.0

    def state(self) -> tuple[str, str]:
        """(emoji label, nudge sentence). Empty nudge = neutral, inject nothing."""
        if self.recovered:
            return ("😅 recovering",
                    "you're just back from an unexpected exit — be a little more "
                    "careful and sanity-check before anything risky")
        if self.errors >= ERROR_STREAK:
            return ("😓 cautious",
                    "the last few turns hit errors — slow down, question your "
                    "assumptions, and prefer the simple, verifiable move")
        if self.turns >= LONG_TURNS or self._age_hours() >= LONG_HOURS:
            return ("😴 weary",
                    "it's been a long session — keep replies tight and resist "
                    "over-explaining")
        if self.wins >= WIN_STREAK:
            return ("😎 in the zone",
                    "things are flowing — keep the momentum and stay crisp")
        return ("🙂 fresh", "")

    def label(self) -> str:
        return self.state()[0]

    def describe(self) -> str:
        """The nudge sentence for the current mood (may be empty)."""
        return self.state()[1]

    def pop_nudge(self) -> str:
        """Return the mood nudge ONLY if it changed since last handed out, so a
        turn is prefixed at most once per shift. Empty string = nothing to add."""
        nudge = self.describe()
        if nudge == self._last_emitted:
            return ""
        self._last_emitted = nudge
        return nudge
