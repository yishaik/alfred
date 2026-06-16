"""Dream mode — an overnight pass that tidies up and prepares the morning (#9).

While you sleep, Alfred does three quiet things:
  * summarise the day  — reuses the daily digest (#7)
  * clean state        — state backup + memory decay (run by the manager)
  * prepare an agenda  — what's scheduled for the next day

The result is a single "morning brief" delivered at the configured hour, so you
wake to a tidy recap-and-plan instead of nothing. The agenda builder is pure
and unit-tested; the manager runs the maintenance and sends the brief.
"""

from .digest import build_digest

AGENDA_HORIZON_S = 86400.0      # how far ahead the agenda looks (24h)


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
    """The morning brief: yesterday's recap plus the day's agenda."""
    parts = ["🌙 𝗺𝗼𝗿𝗻𝗶𝗻𝗴 𝗯𝗿𝗶𝗲𝗳", build_digest(mgr)]
    jobs = mgr.scheduler.list_jobs() if mgr.scheduler else []
    agenda = build_agenda(jobs, now_ts)
    if agenda:
        parts.append(agenda)
    else:
        parts.append("🗓 nothing scheduled for the next 24h")
    return "\n\n".join(parts)
