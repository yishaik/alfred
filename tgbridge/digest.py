"""Daily digest — a short "here's what I did" summary (issue #7).

The health report answers "is the bridge OK"; the digest answers "what
happened today". It's assembled from data the bridge already keeps — the audit
trail (tool calls per agent), today's cost, memory size, and upcoming jobs — so
it costs nothing to produce and never needs an LLM call.

The audit-parsing core is pure and unit-tested; build_digest() wires it to the
live manager. Dream mode (#9) reuses build_digest() as the "what happened"
half of its nightly reflection.
"""

import json
from collections import Counter
from datetime import date

from .config import AUDIT_FILE


def summarize_audit(raw_lines: list, day: str) -> dict:
    """Reduce raw audit JSONL lines to the day's activity. Pure: takes the
    lines and the YYYY-MM-DD prefix, returns counts (no I/O)."""
    tools: Counter = Counter()
    agents: Counter = Counter()
    denials = total = 0
    for ln in raw_lines:
        try:
            e = json.loads(ln)
        except (ValueError, TypeError):
            continue
        if not str(e.get("ts", "")).startswith(day):
            continue
        total += 1
        tools[e.get("tool", "?")] += 1
        agents[e.get("agent", "?")] += 1
        if e.get("decision") == "deny":
            denials += 1
    return {"total": total, "tools": tools, "agents": agents,
            "denials": denials}


def _top(counter: Counter, n: int = 4) -> str:
    return " · ".join(f"{name} {cnt}" for name, cnt in counter.most_common(n))


# Tool -> activity category, for the /costs breakdown (#27). Exact per-tool
# cost isn't available (the SDK bills per turn), so we report where the tool
# *activity* went, which is what the breakdown is really asking.
_CATEGORIES = [
    ("🐚 shell", {"Bash", "PowerShell"}),
    ("📄 files", {"Read", "Write", "Edit", "MultiEdit", "NotebookEdit",
                  "NotebookRead", "Glob", "Grep"}),
    ("🌐 web", {"WebFetch", "WebSearch"}),
]


def categorize_tool(name: str) -> str:
    if name.startswith("mcp__"):
        return "🔧 bridge"
    for label, names in _CATEGORIES:
        if name in names:
            return label
    return "🧩 other"


def tool_breakdown(raw_lines: list, day: str) -> Counter:
    """Pure: tool-call counts grouped by activity category for `day`."""
    cats: Counter = Counter()
    for ln in raw_lines:
        try:
            e = json.loads(ln)
        except (ValueError, TypeError):
            continue
        if not str(e.get("ts", "")).startswith(day):
            continue
        cats[categorize_tool(e.get("tool", "?"))] += 1
    return cats


def build_digest(mgr, day: str | None = None) -> str:
    """Assemble the phone-friendly digest for `day` (default today)."""
    day = day or date.today().isoformat()
    try:
        raw = AUDIT_FILE.read_text(encoding="utf-8", errors="replace").splitlines()
    except OSError:
        raw = []
    a = summarize_audit(raw, day)

    lines = [f"📓 𝗔𝗹𝗳𝗿𝗲𝗱'𝘀 𝗱𝗶𝗴𝗲𝘀𝘁 — {day}"]
    if a["total"]:
        lines.append(f"🛠 {a['total']} tool calls — {_top(a['tools'])}")
        if len(a["agents"]) > 1:
            lines.append(f"🤖 agents: {_top(a['agents'])}")
        if a["denials"]:
            lines.append(f"⛔ {a['denials']} dangerous command(s) blocked")
    else:
        lines.append("🛠 no tracked tool activity today")

    lines.append(f"💰 today ${mgr.today_cost():.2f} · month ${mgr.month_cost():.2f}")

    mem_total = sum(len(m.items) for m in mgr.memories.values())
    if mem_total:
        pinned = sum(1 for m in mgr.memories.values()
                     for it in m.items if it.kind == "pinned")
        lines.append(f"🧠 {mem_total} memories ({pinned} pinned)")

    if mgr.scheduler:
        jobs = mgr.scheduler.list_jobs()
        if jobs:
            nxt = jobs[0]
            lines.append(f"⏰ {len(jobs)} job(s) pending — next: "
                         f"{nxt['text'][:50]} @ {nxt['next_human']}")
    return "\n".join(lines)
