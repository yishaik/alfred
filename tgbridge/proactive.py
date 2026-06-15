"""Proactive commentary — letting an agent speak up without being prompted.

The bridge is otherwise strictly reactive: the agent only runs when a turn is
fed to it. This module adds the one safe, opt-in way for it to volunteer a
thought — an *idle check-in*. When the user has been quiet for a while, a
background loop feeds the agent a lightweight autonomous turn asking it to skim
the recent conversation and, IF there's a genuine open loop worth a nudge, say
so briefly and in character. If there's nothing, it replies with the silence
sentinel and the bridge stays quiet — the agent itself decides whether to speak.

Guardrails (all so an unsolicited ping is never annoying):
  * off by default, per agent (AgentConfig.proactive)
  * only after a long idle gap (PROACTIVE_IDLE_HOURS)
  * never during quiet hours (PROACTIVE_QUIET)
  * the autonomous turn draws from the non-human turn budget, so it's capped
  * at most one check-in per idle stretch (the loop arms again only after the
    user speaks)

The pure helpers here (quiet-hour math, the silence test) are unit-tested; the
loop that uses them lives on the manager.
"""

# What the agent replies with when it has nothing worth saying. Kept short and
# distinctive so the silence test below is unambiguous.
SENTINEL = "NOTHING"

CHECKIN_PROMPT = (
    "[proactive check-in — the user has been idle a while; this turn is "
    "automatic, not from them]\n"
    "Skim our recent conversation for a genuine open loop: something you said "
    "you'd do and didn't, a question left dangling, or a follow-up that's now "
    "worth a gentle nudge. If there is one, say it in ONE or two short lines, "
    "in your own voice — no preamble. If there is honestly nothing worth "
    f"interrupting for, reply with exactly: {SENTINEL}\n"
    "Do not use any tools; just answer."
)


def is_quiet_hour(hour: int, quiet_start: int, quiet_end: int) -> bool:
    """True if `hour` falls in the do-not-disturb window. The window may wrap
    midnight (e.g. 22->8 means 22,23,0..7 are quiet)."""
    if quiet_start == quiet_end:
        return False                      # empty window: never quiet
    if quiet_start < quiet_end:
        return quiet_start <= hour < quiet_end
    return hour >= quiet_start or hour < quiet_end   # wraps midnight


def should_check_in(idle_seconds: float, idle_threshold_seconds: float,
                    now_hour: int, quiet_start: int, quiet_end: int,
                    enabled: bool, busy: bool, already_pinged: bool) -> bool:
    """Pure decision for the idle loop — all the gates in one place so the
    behaviour is testable without a live session or wall clock."""
    if not enabled or busy or already_pinged:
        return False
    if idle_seconds < idle_threshold_seconds:
        return False
    if is_quiet_hour(now_hour, quiet_start, quiet_end):
        return False
    return True


def declined(text: str) -> bool:
    """Did the agent choose silence? True for the sentinel or an empty reply,
    tolerant of trailing punctuation / casing / surrounding whitespace."""
    t = (text or "").strip().rstrip(".!").strip().upper()
    return t == "" or t == SENTINEL
