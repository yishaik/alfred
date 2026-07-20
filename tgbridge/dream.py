"""Dream mode — an overnight pass that tidies up and prepares the morning (#9).

While you sleep, Alfred does four quiet things:
  * synthesise memory — consolidate durable context and retire stale notes
  * summarise the day  — reuses the daily digest (#7)
  * clean state        — state backup + memory decay (run by the manager)
  * prepare an agenda  — what's scheduled for the next day

The result is a single "morning brief" delivered at the configured hour, so you
wake to a tidy recap-and-plan instead of nothing. The agenda builder is pure
and unit-tested; the manager runs the maintenance and sends the brief.
"""

import re
from pathlib import Path

from .digest import build_digest
from .memory_dreaming import schedule_once

AGENDA_HORIZON_S = 86400.0      # how far ahead the agenda looks (24h)
BRAIN_RAW = Path("D:/projects/second-brain/raw")   # x-reader → Second Brain captures


def _front(text: str, key: str) -> str:
    m = re.search(rf"^{key}:\s*(.+)$", text, re.MULTILINE)
    return m.group(1).strip() if m else ""


def second_brain_overnight(now_ts: float, hours: float = 16.0, n: int = 6) -> str:
    """What landed in the Second Brain overnight (last `hours`), newest first —
    the autonomous loop's output, surfaced in the morning brief."""
    if not BRAIN_RAW.is_dir():
        return ""
    cutoff = now_ts - hours * 3600
    recent = []
    for p in BRAIN_RAW.glob("*.md"):
        try:
            if p.stat().st_mtime < cutoff:
                continue
            head = p.read_text(encoding="utf-8")[:800]
        except OSError:
            continue
        title = _front(head, "title") or _front(head, "author") or p.stem
        typ = _front(head, "type")
        recent.append((p.stat().st_mtime, title, typ))
    if not recent:
        return ""
    recent.sort(reverse=True)
    out = [f"🧠 overnight in your brain ({len(recent)} new):"]
    for _, title, typ in recent[:n]:
        tag = f"  ·{typ}" if typ in ("x-article", "synthesis") else ""
        out.append(f"• {title[:80]}{tag}")
    if len(recent) > n:
        out.append(f"  …and {len(recent) - n} more")
    return "\n".join(out)


def open_todos(mgr, n: int = 8) -> str:
    todos = getattr(mgr, "todos", None)
    items = [t for t in (todos.items if todos else []) if t.status != "done"]
    if not items:
        return ""
    out = [f"📋 open tasks ({len(items)}):"]
    for t in items[:n]:
        out.append(f"{'🔄' if t.status == 'doing' else '•'} {t.text[:70]}")
    return "\n".join(out)


def build_agenda(jobs: list, now_ts: float,
                 horizon_s: float = AGENDA_HORIZON_S) -> str:
    """Pure: from scheduled-job dicts, the lines for jobs firing within the
    horizon, soonest first. Empty string when nothing's coming up."""
    soon = sorted((j for j in jobs
                   if now_ts <= j.get("next_ts", 0) <= now_ts + horizon_s),
                  key=lambda j: j["next_ts"])
    if not soon:
        return ""
    out = [f"🗓 coming up ({len(soon)}):"]
    for j in soon:
        kind = "🔔" if j.get("kind") == "remind" else "▶️"
        out.append(f"{kind} {j.get('next_human', '?')} — {j.get('text', '')[:60]}")
    return "\n".join(out)


def dream_brief(mgr, now_ts: float) -> str:
    """The morning brief plus a non-blocking memory consolidation pass."""
    # Memory synthesis is deliberately detached from brief delivery: a slow or
    # failed model call must never delay the user's morning message.
    schedule_once(mgr)

    parts = ["🌙 𝗺𝗼𝗿𝗻𝗶𝗻𝗴 𝗯𝗿𝗶𝗲𝗳"]
    for section in (second_brain_overnight(now_ts), open_todos(mgr)):
        if section:
            parts.append(section)
    parts.append(build_digest(mgr))
    jobs = mgr.scheduler.list_jobs() if mgr.scheduler else []
    agenda = build_agenda(jobs, now_ts)
    parts.append(agenda if agenda else "🗓 nothing scheduled for the next 24h")
    return "\n\n".join(parts)
