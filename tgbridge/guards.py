"""Hooks: PreToolUse guardrails + audit trail, PostToolUse diff previews.

Purpose:  PreToolUse guardrails, the audit trail, and PostToolUse diff previews.
Inputs:   SDK hook payloads for every tool call; danger patterns from config.
Outputs:  Allow/deny decisions, state/audit.jsonl rows, compact diffs to the chat.
Key fns:  build_hooks, is_dangerous, audit, rotate_audit, render_diff.
Deps:     config, fmt, claude_agent_sdk; the session supplies the permission prompt.
Note:     Hooks fire even under bypassPermissions — this layer still protects you.
Updated:  2026-07-31

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
    # --- macOS/Linux equivalents of the Windows entries above ---
    # persistence (the launchd/cron analogue of `schtasks /create`)
    r"\blaunchctl\s+(load|bootstrap|enable)\b",
    r"\bcrontab\s+[^\n]*-r?\s*[a-z/]",                         # crontab install/remove
    # disabling OS security (the analogue of `netsh firewall`)
    r"\bcsrutil\s+disable\b",                                  # System Integrity Protection
    r"\bspctl\s+--master-disable\b",                           # Gatekeeper
    r"\bsocketfilterfw\b[^\n]*--setglobalstate\s+off",         # macOS firewall
    r"\bdefaults\s+write\b[^\n]*com\.apple\.(loginwindow|security)",
    # privilege escalation / account creation
    r"\bdscl\s+\.\s+-(create|delete)\b",                       # local account changes
    r"\bsudo\s+(rm|dd|mkfs|chown|chmod)\b",
    r"\bdd\b[^\n]*\bof=/dev/(disk|sd|nvme)",                   # raw device write
    r"\bdiskutil\s+(eraseDisk|eraseVolume|reformat)\b",
    # --- exfiltration (added 2026-09-04: the list above was destruction-only,
    # so under auto-approve an injected turn could ship .env anywhere unasked)
    # secret files handed to a network client, by any of the usual flags
    r"\b(curl|wget|nc|ncat|scp|rsync|sftp|ftp)\b[^\n]*"
    r"(vault\.json|\.env\b|\.env-[a-z]|toolrpc\.token|grants\.json|"
    r"secretbackup\.recipient|\.credentials\.json|\.ssh/|\.token-)",
    r"\b(curl|wget)\b[^\n]*(-T\s|--upload-file\b|--data-binary\s+@|-d\s+@|-F\s+[^\s]*=@)",
    # environment dumps (tokens live there) and echoing secret-shaped vars
    r"(^|[;&|]\s*)(env|printenv|set)\s*($|[|>])",
    r"/proc/(self|\d+)/environ",
    r"\becho\b[^\n]*\$\{?[A-Z_]*(TOKEN|KEY|SECRET|PASSWORD)",
    # pushing somewhere new
    r"\bgit\s+remote\s+(add|set-url)\b",
    r"\bgit\s+push\b[^\n]*https?://",
    # reverse shells / raw sockets
    r"\b(nc|ncat|bash|sh)\b[^\n]*(-e\s*/bin/|/dev/tcp/)",
] + EXTRA_DANGER_PATTERNS]

# Files that must never be read by the agent, by any tool (CLAUDE.md: "never
# cat .env or vault.json"). Matched against Bash commands AND the file_path /
# path arguments of Read/Grep/Edit/Write/Glob, and denied outright — no tap.
SECRET_PATH_RX = re.compile(
    r"(^|[\s/'\"=])(vault\.json|\.env(-[A-Za-z0-9_]+)?|toolrpc\.token|grants\.json|"
    r"secretbackup\.recipient|\.credentials\.json|\.token-[A-Za-z0-9_-]+|"
    r"\.ssh/(id_[a-z0-9]+|authorized_keys|config)|\.netrc|\.pgpass|\.npmrc)"
    r"(\s|$|['\"|;&)])", re.IGNORECASE)
_READERS_RX = re.compile(
    r"\b(cat|less|more|head|tail|grep|rg|sed|awk|strings|base64|xxd|od|hexdump|"
    r"cp|mv|tee|python3?|node|jq|source|\.)\b", re.IGNORECASE)
_PATH_TOOLS = ("Read", "Edit", "MultiEdit", "Write", "NotebookEdit", "Grep", "Glob")
# state-changing tools that a bot-originated turn may not use without a tap
PEER_GATED_TOOLS = frozenset((
    "Bash", "PowerShell", "Write", "Edit", "MultiEdit", "NotebookEdit",
    "mcp__bridge__message_agent", "mcp__bridge__schedule",
    "mcp__bridge__unschedule", "mcp__bridge__forget", "mcp__bridge__send_file"))


def is_forbidden(tool_name: str, tool_input: dict) -> str | None:
    """A secret file about to be read/opened: return the reason, or None.
    Hard deny (no Telegram tap): there is no legitimate reason for the agent
    to see a credential value — secretbox --run exists for using one."""
    if tool_name in ("Bash", "PowerShell"):
        cmd = tool_input.get("command") or ""
        # `secretbox.py --run FOO` legitimately names the key, never the file;
        # only flag commands that both name a secret file and can read it.
        if SECRET_PATH_RX.search(cmd) and _READERS_RX.search(cmd) \
                and "secretbox.py" not in cmd:
            return "reads a secret file"
        return None
    if tool_name in _PATH_TOOLS:
        target = (tool_input.get("file_path") or tool_input.get("notebook_path")
                  or tool_input.get("path") or "")
        if tool_name == "Grep" and not target:
            target = tool_input.get("pattern", "")
        if target and SECRET_PATH_RX.search(" " + target.replace("\\", "/") + " "):
            return "reads a secret file"
    return None


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


# Token shapes we know; anything matching is masked before it hits a log.
_SECRET_SHAPES = [re.compile(p) for p in (
    r"sk-[A-Za-z0-9_-]{16,}",                     # OpenAI / Anthropic
    r"sbp_[A-Za-z0-9]{20,}",                      # Supabase
    r"gsk_[A-Za-z0-9]{20,}",                      # Groq
    r"AIza[0-9A-Za-z_-]{30,}",                    # Google
    r"gh[pousr]_[A-Za-z0-9]{30,}",                # GitHub
    r"xox[abpr]-[A-Za-z0-9-]{20,}",               # Slack
    r"\b\d{8,10}:[A-Za-z0-9_-]{35}\b",            # Telegram bot token
    r"eyJ[A-Za-z0-9_-]{20,}\.[A-Za-z0-9_-]{10,}\.[A-Za-z0-9_-]{10,}",  # JWT
    r"(?i)bearer\s+[A-Za-z0-9._~+/=-]{16,}",
    r"(?i)\b[A-Z0-9_]*(TOKEN|KEY|SECRET|PASSWORD)[A-Z0-9_]*=['\"]?[^\s'\"]{8,}",
)]


def redact(text: str) -> str:
    """Mask credential-shaped substrings (keeps the first 4 chars as a hint)."""
    def _mask(m):
        v = m.group(0)
        if "=" in v and v.upper().split("=", 1)[0].rstrip("'\"") == v.split("=", 1)[0].rstrip("'\"").upper():
            k, _, _ = v.partition("=")
            return f"{k}=***"
        return v[:4] + "***"
    for rx in _SECRET_SHAPES:
        text = rx.sub(_mask, text)
    return text


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
                "summary": redact(summarize_tool(tool, tool_input))[:300],
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
        forbidden = is_forbidden(tool, tool_input)
        if forbidden:
            metrics.bump("guard_deny")
            audit(session.cfg.name, session.skey, tool, tool_input,
                  guarded=True, decision="deny-secret")
            session.outbox.emit(f"⛔ blocked {tool}: {forbidden} "
                                f"(`{redact(summarize_tool(tool, tool_input))[:120]}`)")
            return {
                "hookSpecificOutput": {
                    "hookEventName": "PreToolUse",
                    "permissionDecision": "deny",
                    "permissionDecisionReason":
                        "[bridge] credentials are never read directly. To USE "
                        "a secret run the command through "
                        "`python3 /home/ubuntu/alfred/secretbox.py --run KEY -- <cmd>`.",
                }
            }
        matched = is_dangerous(tool, tool_input)
        # A turn that came from another agent (peer bus / bot message) is
        # untrusted input; anything that changes state needs a human tap even
        # under auto-approve. Read-only tools stay free.
        if not matched and getattr(session, "turn_source", None) is not None \
                and session.turn_source.kind == "bot" and tool in PEER_GATED_TOOLS:
            matched = f"peer-originated turn ({session.turn_source.origin or 'bot'})"
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
