"""Hooks: PreToolUse guardrails + audit trail, PostToolUse diff previews.

Hooks fire for EVERY tool call regardless of permission mode, so this is the
layer that still protects you when auto-approve is on: shell commands matching
a danger pattern require a Telegram tap before they run. Everything is also
appended to state/audit.jsonl, and file edits are echoed as compact diffs.
"""

import difflib
import json
import logging
import re
import time

from claude_agent_sdk import HookMatcher

from . import metrics, tracing
from .config import (AUDIT_FILE, EXTRA_DANGER_PATTERNS, PERMISSION_TIMEOUT,
                     SHOW_DIFFS)
from .fmt import summarize_tool

log = logging.getLogger("bridge.guards")

DANGER_PATTERNS: list[re.Pattern] = [re.compile(p, re.IGNORECASE) for p in [
    r"\brm\s+(-[a-z]*r[a-z]*f|-[a-z]*f[a-z]*r)[a-z]*\b",      # rm -rf / -fr
    r"\brmdir\s+/s\b",
    r"\bdel\s+(/[fsq]\s+)*[/\\]",                              # del /f /s /q on roots
    r"remove-item\b.*-recurse\b.*-force|remove-item\b.*-force\b.*-recurse",
    r"\bformat(\.com)?\s+[a-z]:",
    r"\bmkfs\b",
    r"\bdiskpart\b",
    r"\bgit\s+push\b.*(--force\b|-f\b)",
    r"\bgit\s+reset\s+--hard\b",
    r"\bgit\s+clean\b.*-[a-z]*f",
    r"\bshutdown\b|\bRestart-Computer\b|\bStop-Computer\b",
    r"\breg\s+(add|delete)\s+HKLM|\bSet-ItemProperty\b.*HKLM",
    r"\bdrop\s+(table|database)\b",
    r"\btaskkill\s+/f\s+/im\s+(?!claude|node)",                # broad force-kills
    r"\bbcdedit\b|\bvssadmin\s+delete\b|\bcipher\s+/w\b",
    # download piped straight into a shell/interpreter
    r"\b(curl|wget|iwr|invoke-webrequest|invoke-restmethod)\b[^\n|]*\|[^\n|]*"
    r"\b(sh|bash|zsh|iex|invoke-expression|python)\b",
    r"\bschtasks\b[^\n]*/create",                              # persistence
    r"\bnetsh\s+(advfirewall|firewall)\b",
    r"\bgit\s+push\b.*--mirror\b",
] + EXTRA_DANGER_PATTERNS]


def is_dangerous(tool_name: str, tool_input: dict) -> str | None:
    """Return the matched pattern (for display) or None."""
    if tool_name not in ("Bash", "PowerShell"):
        return None
    cmd = tool_input.get("command") or ""
    for rx in DANGER_PATTERNS:
        m = rx.search(cmd)
        if m:
            return m.group(0)
    return None


def rotate_audit(path=None, max_bytes: int = 10 * 1024 * 1024,
                 keep: int = 3) -> bool:
    """Archive the audit log as audit-YYYYMMDD-HHMM.jsonl once it grows past
    max_bytes; keep only the newest archives. Returns True if rotated."""
    path = path or AUDIT_FILE
    if not (path.exists() and path.stat().st_size > max_bytes):
        return False
    stamp = time.strftime("%Y%m%d-%H%M")
    path.replace(path.with_name(f"{path.stem}-{stamp}.jsonl"))
    archives = sorted(path.parent.glob(f"{path.stem}-*.jsonl"))
    for old in archives[:-keep]:
        try:
            old.unlink()
        except OSError:
            pass
    return True


def audit(agent: str, skey: str, tool: str, tool_input: dict,
          guarded: bool = False, decision: str = ""):
    try:
        rotate_audit()
        with AUDIT_FILE.open("a", encoding="utf-8") as fh:
            fh.write(json.dumps({
                "ts": time.strftime("%Y-%m-%dT%H:%M:%S"),
                "agent": agent, "skey": skey, "tool": tool,
                "summary": summarize_tool(tool, tool_input)[:300],
                "guarded": guarded, "decision": decision,
            }, ensure_ascii=False) + "\n")
    except Exception:
        log.exception("audit write failed")


def render_diff(tool: str, tool_input: dict, max_lines: int = 22) -> str | None:
    """Compact human diff for a file-editing tool call, or None."""
    path = tool_input.get("file_path") or tool_input.get("notebook_path") or "?"
    name = path.replace("\\", "/").rsplit("/", 1)[-1]
    edits = []
    if tool == "Edit":
        edits = [tool_input]
    elif tool == "MultiEdit":
        edits = tool_input.get("edits") or []
    elif tool == "Write":
        content = tool_input.get("content") or ""
        return (f"📝 wrote {name} "
                f"({len(content.splitlines())} lines, {len(content)} chars)")
    elif tool == "NotebookEdit":
        return f"✏️ edited notebook {name}"
    if not edits:
        return None
    lines: list[str] = []
    for e in edits:
        old = (e.get("old_string") or "").splitlines()
        new = (e.get("new_string") or "").splitlines()
        for ln in difflib.unified_diff(old, new, lineterm="", n=1):
            if ln.startswith(("---", "+++", "@@")):
                continue
            lines.append(ln)
        lines.append("···")
    while lines and lines[-1] == "···":
        lines.pop()
    if not lines:
        return None
    truncated = len(lines) > max_lines
    body = "\n".join(ln[:120] for ln in lines[:max_lines])
    # ```diff so Telegram colorizes the +/- lines.
    return (f"✏️ {name}\n```diff\n{body}\n"
            + ("… (truncated)\n" if truncated else "") + "```")


def build_hooks(session) -> dict:
    """Returns the `hooks=` dict for ClaudeAgentOptions."""

    async def pre_tool_use(input_data, tool_use_id, context):
        tool = input_data.get("tool_name", "?")
        tool_input = input_data.get("tool_input") or {}
        tracing.start(tool_use_id, tool, summarize_tool(tool, tool_input))
        matched = is_dangerous(tool, tool_input)
        if not matched:
            audit(session.cfg.name, session.skey, tool, tool_input)
            return {}
        # In approvals mode can_use_tool already prompts; don't double-prompt.
        if not session.cfg.auto_approve:
            audit(session.cfg.name, session.skey, tool, tool_input,
                  guarded=True, decision="deferred-to-approvals")
            return {}
        allowed = await session.guard_approve(tool, tool_input, matched)
        if not allowed:
            metrics.bump("guard_deny")
        audit(session.cfg.name, session.skey, tool, tool_input,
              guarded=True, decision="allow" if allowed else "deny")
        return {
            "hookSpecificOutput": {
                "hookEventName": "PreToolUse",
                "permissionDecision": "allow" if allowed else "deny",
                "permissionDecisionReason":
                    "user approved via Telegram" if allowed else
                    "[bridge] user denied this dangerous command via Telegram",
            }
        }

    async def post_trace(input_data, tool_use_id, context):
        """Close the trace span for every tool, recording ok/error outcome."""
        try:
            resp = input_data.get("tool_response")
            errored = (isinstance(resp, dict) and (resp.get("is_error") or resp.get("isError"))) \
                or (isinstance(resp, str) and resp.strip().lower().startswith("error"))
            tracing.finish(session.skey, tool_use_id, "error" if errored else "ok")
        except Exception:
            log.exception("trace finish failed")
        return {}

    async def post_tool_use(input_data, tool_use_id, context):
        try:
            diff = render_diff(input_data.get("tool_name", ""),
                               input_data.get("tool_input") or {})
            if diff:
                session.outbox.emit(diff)
        except Exception:
            log.exception("diff render failed")
        return {}

    hooks = {
        # generous timeout: a human has to find their phone
        "PreToolUse": [HookMatcher(matcher=None, hooks=[pre_tool_use],
                                   timeout=PERMISSION_TIMEOUT + 60)],
        # trace EVERY tool's outcome + duration (concept #19)
        "PostToolUse": [HookMatcher(matcher=None, hooks=[post_trace], timeout=30)],
    }
    if SHOW_DIFFS:
        hooks["PostToolUse"].append(HookMatcher(
            matcher="Edit|MultiEdit|Write|NotebookEdit",
            hooks=[post_tool_use], timeout=30))
    return hooks
