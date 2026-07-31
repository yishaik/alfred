"""Per-turn tool-call tracing (concept #19 from the agentic-engineering audit).

Purpose:  Per-turn tool-call timeline — how each call went, not just that it ran.
Inputs:   Tool start/finish events from the hooks, kept per session.
Outputs:  Spans (duration + ok/error) rendered as the /trace timeline.
Key fns:  start, finish, render.
Deps:     config; guards emits the events.
Note:     audit.jsonl records that a tool ran; this records how it went.
Updated:  2026-07-31

The audit log (guards.audit → audit.jsonl) records *that* a tool ran. Tracing
adds *how it went*: wall-clock duration and ok/error outcome, kept per session so
`/trace` can show the recent tool-call sequence as a readable timeline — the
difference between "what the agent said it did" and "what actually happened".

Spans are opened on PreToolUse and closed on PostToolUse, keyed by tool_use_id.
In-memory (a small ring per session) for `/trace`; also appended to
state/traces.jsonl for history.
"""

import json
import logging
import time
from collections import deque

from .config import STATE_DIR

log = logging.getLogger("bridge.tracing")
TRACES_FILE = STATE_DIR / "traces.jsonl"

_open: dict[str, tuple[float, str, str]] = {}        # tool_use_id -> (start, tool, summary)
_recent: dict[str, deque] = {}                       # skey -> deque[span]


def start(tool_use_id: str, tool: str, summary: str) -> None:
    _open[tool_use_id] = (time.monotonic(), tool, (summary or "")[:120])


def finish(skey: str, tool_use_id: str, status: str) -> None:
    rec = _open.pop(tool_use_id, None)
    if rec is None:
        return
    start_ts, tool, summary = rec
    span = {"tool": tool, "summary": summary, "status": status,
            "ms": int((time.monotonic() - start_ts) * 1000),
            "at": time.strftime("%H:%M:%S")}
    _recent.setdefault(skey, deque(maxlen=50)).append(span)
    try:
        # One line per tool call with no rotation grew unbounded; cap it the same
        # way the audit log is capped (nothing ever reads this file back).
        if TRACES_FILE.exists() and TRACES_FILE.stat().st_size > 5 * 1024 * 1024:
            TRACES_FILE.replace(TRACES_FILE.with_name(
                f"{TRACES_FILE.stem}-{time.strftime('%Y%m%d-%H%M')}.jsonl"))
            for old in sorted(TRACES_FILE.parent.glob(f"{TRACES_FILE.stem}-*.jsonl"))[:-2]:
                try:
                    old.unlink()
                except OSError:
                    pass
        with TRACES_FILE.open("a", encoding="utf-8") as fh:
            fh.write(json.dumps({"skey": skey, **span}, ensure_ascii=False) + "\n")
    except Exception:
        log.exception("trace write failed")


def render(skey: str, n: int = 18) -> str:
    """A compact timeline of this session's recent tool calls."""
    spans = list(_recent.get(skey, deque()))[-n:]
    if not spans:
        return "🧭 no tool calls traced yet this session."
    total = sum(s["ms"] for s in spans)
    errs = sum(1 for s in spans if s["status"] == "error")
    slow = max(spans, key=lambda s: s["ms"])
    out = [f"🧭 last {len(spans)} tool calls · {total} ms total · {errs} error(s)"
           f" · slowest {slow['tool']} {slow['ms']}ms"]
    for s in spans:
        mark = "✅" if s["status"] == "ok" else "❌"
        out.append(f"{mark} {s['tool']} · {s['ms']}ms · {s['summary'][:64]}")
    return "\n".join(out)
