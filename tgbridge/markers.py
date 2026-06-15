"""Marker protocol: structured directives Claude embeds in its replies.

    ⟦SEND:<absolute path>⟧             send a file/photo to the user
    ⟦BUTTONS:label|label|...⟧          attach quick-reply buttons to this message
    ⟦TO:<agent-or-peer>|<message>⟧     bot-to-bot message (rate-limited, hop-capped)
    ⟦REMIND:<when>|<text>⟧             bridge texts the user at <when>
    ⟦SCHEDULE:<when>|<prompt>⟧         bridge feeds <prompt> to this agent at <when>
    ⟦UNSCHEDULE:<job id>⟧              cancel a scheduled job

<when> accepts: "2026-06-09 15:00" / "2026-06-09T15:00", "15:00" (next occurrence),
"+30m" / "+2h" / "+90s", "daily 09:00" (recurring).
"""

import re
from dataclasses import dataclass, field
from datetime import datetime, timedelta

SEND_RE = re.compile(r"⟦SEND:(.*?)⟧")
BUTTONS_RE = re.compile(r"⟦BUTTONS:(.*?)⟧")
TO_RE = re.compile(r"⟦TO:([^|⟧]+)\|(.*?)⟧", re.DOTALL)
REMIND_RE = re.compile(r"⟦REMIND:([^|⟧]+)\|(.*?)⟧", re.DOTALL)
SCHEDULE_RE = re.compile(r"⟦SCHEDULE:([^|⟧]+)\|(.*?)⟧", re.DOTALL)
UNSCHEDULE_RE = re.compile(r"⟦UNSCHEDULE:([^⟧]+)⟧")

_ALL = [SEND_RE, BUTTONS_RE, TO_RE, REMIND_RE, SCHEDULE_RE, UNSCHEDULE_RE]


@dataclass
class Parsed:
    text: str = ""                      # visible text with markers stripped
    sends: list[str] = field(default_factory=list)
    buttons: list[str] = field(default_factory=list)
    to: list[tuple[str, str]] = field(default_factory=list)        # (dest, message)
    reminds: list[tuple[str, str]] = field(default_factory=list)   # (when, text)
    schedules: list[tuple[str, str]] = field(default_factory=list)  # (when, prompt)
    unschedules: list[str] = field(default_factory=list)


def parse(text: str) -> Parsed:
    p = Parsed()
    p.sends = [m.strip().strip('"') for m in SEND_RE.findall(text)]
    for m in BUTTONS_RE.findall(text):
        p.buttons += [b.strip()[:60] for b in m.split("|") if b.strip()]
    p.buttons = p.buttons[:8]
    p.to = [(d.strip(), t.strip()) for d, t in TO_RE.findall(text)]
    p.reminds = [(w.strip(), t.strip()) for w, t in REMIND_RE.findall(text)]
    p.schedules = [(w.strip(), t.strip()) for w, t in SCHEDULE_RE.findall(text)]
    p.unschedules = [u.strip() for u in UNSCHEDULE_RE.findall(text)]
    for rx in _ALL:
        text = rx.sub("", text)
    p.text = text.strip()
    return p


_REL_RE = re.compile(r"^\+(\d+)\s*([smhd])$", re.IGNORECASE)
_HHMM_RE = re.compile(r"^(\d{1,2}):(\d{2})$")
_DAILY_RE = re.compile(r"^(?:daily|every\s*day)\s+(\d{1,2}):(\d{2})$", re.IGNORECASE)
_WEEKLY_RE = re.compile(
    r"^(?:weekly|every)\s+(mon|tue|wed|thu|fri|sat|sun)[a-z]*\s+(\d{1,2}):(\d{2})$",
    re.IGNORECASE)
_WEEKDAYS_RE = re.compile(r"^weekdays\s+(\d{1,2}):(\d{2})$", re.IGNORECASE)
_DAYS = ["mon", "tue", "wed", "thu", "fri", "sat", "sun"]


def _at(now: datetime, h: int, mi: int) -> datetime:
    return now.replace(hour=h, minute=mi, second=0, microsecond=0)


def next_fire(recur: str, after: datetime) -> datetime:
    """Next occurrence of a recur spec strictly after `after`."""
    dt, r = parse_when(recur, after)
    if r is None:
        raise ValueError(f"not a recurrence: {recur}")
    return dt


def parse_when(when: str, now: datetime | None = None) -> tuple[datetime, str | None]:
    """Return (next_fire_local, recur_spec|None). Raises ValueError on junk.
    recur specs: 'daily HH:MM', 'weekly <day> HH:MM', 'weekdays HH:MM'."""
    now = now or datetime.now()
    when = when.strip()

    m = _DAILY_RE.match(when)
    if m:
        h, mi = int(m.group(1)), int(m.group(2))
        nxt = _at(now, h, mi)
        if nxt <= now:
            nxt += timedelta(days=1)
        return nxt, f"daily {h:02d}:{mi:02d}"

    m = _WEEKLY_RE.match(when)
    if m:
        day = m.group(1).lower()
        h, mi = int(m.group(2)), int(m.group(3))
        nxt = _at(now, h, mi)
        ahead = (_DAYS.index(day) - nxt.weekday()) % 7
        nxt += timedelta(days=ahead)
        if nxt <= now:
            nxt += timedelta(days=7)
        return nxt, f"weekly {day} {h:02d}:{mi:02d}"

    m = _WEEKDAYS_RE.match(when)
    if m:
        h, mi = int(m.group(1)), int(m.group(2))
        nxt = _at(now, h, mi)
        while nxt <= now or nxt.weekday() > 4:
            nxt += timedelta(days=1)
            nxt = _at(nxt, h, mi)
        return nxt, f"weekdays {h:02d}:{mi:02d}"

    m = _REL_RE.match(when)
    if m:
        n, unit = int(m.group(1)), m.group(2).lower()
        delta = {"s": timedelta(seconds=n), "m": timedelta(minutes=n),
                 "h": timedelta(hours=n), "d": timedelta(days=n)}[unit]
        return now + delta, None

    m = _HHMM_RE.match(when)
    if m:
        h, mi = int(m.group(1)), int(m.group(2))
        if not (0 <= h < 24 and 0 <= mi < 60):
            raise ValueError(f"bad time: {when}")
        nxt = now.replace(hour=h, minute=mi, second=0, microsecond=0)
        if nxt <= now:
            nxt += timedelta(days=1)
        return nxt, None

    for f in ("%Y-%m-%dT%H:%M", "%Y-%m-%d %H:%M", "%Y-%m-%dT%H:%M:%S",
              "%Y-%m-%d %H:%M:%S", "%Y-%m-%d"):
        try:
            return datetime.strptime(when, f), None
        except ValueError:
            continue
    raise ValueError(f"can't parse time: {when!r}")
