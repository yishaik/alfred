"""Jev instant-compaction for the bridge (advisory /jevcompact).

Scores each tool call in the current Claude Code transcript for relevance to the
latest objective (Jev Noul, batched) and reports what could be pruned + the token
savings — the "instant compaction" idea, on demand. Advisory: Claude Code still owns
its own context window; this shows the dead weight rather than mutating the session.

Guardrail: the most recent ALWAYS_KEEP tool calls are never flagged (in-flight work).
Key: /home/ubuntu/alfred/.jev_key (or env JEV_API_KEY). Never raises to the caller.
"""
from __future__ import annotations
import json, os, pathlib, time, urllib.request

JEV_URL = "https://api.typesafe.ai/v1/systemone"
ALWAYS_KEEP = 15      # newest N tool calls are always kept (recency floor)
MAX_SCORE = 120       # cap Jev work per run
CHUNK = 25
KEEP_THRESHOLD = 0.4


def _key() -> str:
    k = os.environ.get("JEV_API_KEY", "").strip()
    if k:
        return k
    try:
        return pathlib.Path("/home/ubuntu/alfred/.jev_key").read_text().strip()
    except Exception:
        return ""


def _text(content) -> str:
    if isinstance(content, str):
        return content
    if isinstance(content, list):
        return " ".join(b.get("text", "") for b in content
                        if isinstance(b, dict) and b.get("type") == "text")
    return ""


def _summ(name: str, inp: dict) -> str:
    if not isinstance(inp, dict):
        return name
    if name == "Bash":
        return f"Bash: {str(inp.get('command',''))[:140]}"
    return f"{name}: " + str({k: str(v)[:50] for k, v in list(inp.items())[:3]})


def _parse(path: str):
    last_user, pending, items = "", {}, []
    with open(path, encoding="utf-8", errors="replace") as fh:
        for line in fh:
            try:
                o = json.loads(line)
            except Exception:
                continue
            content = (o.get("message") or {}).get("content")
            if o.get("type") == "user":
                t = _text(content).strip()
                if t and not t.startswith("<"):
                    last_user = t[:400]
            if isinstance(content, list):
                for b in content:
                    if not isinstance(b, dict):
                        continue
                    if b.get("type") == "tool_use":
                        pending[b.get("id")] = _summ(b.get("name", "?"), b.get("input", {}))
                    elif b.get("type") == "tool_result":
                        summ = pending.pop(b.get("tool_use_id"), None)
                        if summ is not None:
                            rc = len(_text(b.get("content")) or "")
                            items.append({"id": f"t{len(items)}", "summary": summ, "result_chars": rc})
    return last_user, items


def _score(objective: str, chunk: list) -> dict:
    questions = {it["id"]: {
        "type": "noul",
        "instructions": f"Objective: {objective}\n\nIs this earlier tool call's result still likely needed in context to keep pursuing the objective?\nTool call: {it['summary']}",
        "criteria": {"true": "still relevant / likely reused", "false": "one-off, superseded, or unrelated"},
    } for it in chunk}
    body = json.dumps({"state": {"objective": objective}, "model": "jev-latest", "questions": questions}).encode()
    req = urllib.request.Request(JEV_URL, body, {"Authorization": "Bearer " + _key(), "Content-Type": "application/json"})
    d = json.loads(urllib.request.urlopen(req, timeout=45).read())
    return {k: float(a.get("noul", 0.5)) for k, a in (d.get("answers") or {}).items()}


def report(path: str) -> str:
    """Formatted, Telegram-friendly advisory. Never raises."""
    try:
        if not _key():
            return "🗜 Jev compaction: no Jev key configured."
        objective, items = _parse(path)
        if not objective:
            objective = "(no explicit recent objective)"
        if len(items) <= ALWAYS_KEEP:
            return f"🗜 Jev compaction: only {len(items)} tool calls — nothing to prune (recency floor {ALWAYS_KEEP})."
        # never touch the newest ALWAYS_KEEP; score a bounded window before that
        prunable = items[:-ALWAYS_KEEP][-MAX_SCORE:]
        t0 = time.time()
        probs = {}
        for i in range(0, len(prunable), CHUNK):
            probs.update(_score(objective, prunable[i:i + CHUNK]))
        elapsed = time.time() - t0
        drop = [it for it in prunable if probs.get(it["id"], 1.0) < KEEP_THRESHOLD]
        saved = sum(it["result_chars"] for it in drop)
        total = sum(it["result_chars"] for it in prunable) or 1
        lines = [
            "🗜 *Jev instant-compaction* (advisory)",
            f"objective: {objective[:90]}",
            f"scored {len(prunable)} tool calls in {elapsed:.1f}s · recency-kept last {ALWAYS_KEEP}",
            f"prunable: *{len(drop)}* → ~*{saved//4}* tokens ({100*saved//total}% of scored tool output)",
        ]
        top = sorted(drop, key=lambda x: probs.get(x["id"], 0))[:6]
        if top:
            lines.append("\ntop dead weight:")
            lines += [f"· {probs.get(it['id'],0):.2f} {it['summary'][:70]}" for it in top]
        lines.append("\n_Advisory — Claude Code owns the live window; run /compact to actually shrink it._")
        return "\n".join(lines)
    except Exception as e:
        return f"🗜 Jev compaction failed: {str(e)[:160]}"
