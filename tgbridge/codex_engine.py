"""Codex as an alternative agent backend, alongside Claude Code.

Purpose:  Run a turn through the ChatGPT/Codex subscription instead of Claude.
Inputs:   A prompt, the agent's stored Codex thread id, the working directory.
Outputs:  The reply text; the thread id is persisted for continuity.
Key fns:  available(), run_turn(), is_usage_exhausted(), thread_for/clear_thread.
Deps:     the `codex` CLI (~/.local/bin/codex), authenticated against ChatGPT.
Updated:  2026-08-02

Why a parallel path and not a port
----------------------------------
session.py is ~1300 lines built on claude_agent_sdk: in-process MCP tools that
close over the session object, a can_use_tool callback, guard hooks, partial
message streaming, file checkpointing. Codex has none of those shapes — it
takes MCP servers over stdio only, has its own sandbox and approval model, and
emits completed items rather than deltas.

Rewriting that surface to be backend-neutral is a large change with real
regression risk on the bridge the owner is talking through. So this is
additive: a second path hanging off the same seam the model router already
uses in feed(), leaving the Claude path untouched. Switching back is one
command, and if this file were deleted the bridge would behave exactly as it
did before.

What you give up in codex mode
------------------------------
The bridge's own tools — send_file, remember/recall, schedule, message_agent —
are Python closures over the live session, so they cannot cross a stdio MCP
boundary as they stand. They are unavailable here until they are re-exposed as
a real MCP server. Guard hooks and per-tool approval do not apply either;
Codex enforces its own sandbox. Replies arrive whole rather than streaming.

What still works: conversation with continuity, and Codex's own tools — it
reads and edits files and runs commands in the working directory.
"""
from __future__ import annotations

import asyncio
import json
import logging
import os
import shutil
from pathlib import Path

from .config import STATE_DIR, load_json, save_json

log = logging.getLogger("bridge.codex")

CODEX_BIN = os.environ.get("BRIDGE_CODEX_BIN", "") or shutil.which("codex") or \
    str(Path.home() / ".local/bin/codex")

# One thread id per agent, so /engine codex resumes where it left off rather
# than starting fresh on every message.
_STATE_FILE = STATE_DIR / "codex-threads.json"
_saved = load_json(_STATE_FILE, {})
_threads: dict[str, str] = _saved.get("threads", {})
# Optional per-agent model override (else codex uses its configured default).
_models: dict[str, str] = _saved.get("models", {})


def _persist():
    try:
        save_json(_STATE_FILE, {"threads": _threads, "models": _models})
    except OSError:
        log.exception("could not persist Codex thread state")


def default_model() -> str | None:
    import tomllib
    home = Path(os.environ.get("CODEX_HOME", str(Path.home() / ".codex")))
    try:
        return tomllib.loads((home / "config.toml").read_text(encoding="utf-8")).get("model")
    except (OSError, ValueError):
        return None


def get_model(agent: str) -> str | None:
    return _models.get(agent)


def set_model(agent: str, model: str | None) -> None:
    if model:
        _models[agent] = model
    else:
        _models.pop(agent, None)
    # exec resume supports -m: keep the thread and all native tool context.
    _persist()


def list_models() -> list[tuple[str, str]]:
    """Selectable codex models from ~/.codex/models_cache.json (internal
    slugs like gpt-reserve / codex-auto-review filtered out)."""
    try:
        data = json.loads((Path(os.environ.get("CODEX_HOME", str(Path.home() / ".codex"))) / "models_cache.json").read_text(encoding="utf-8"))
        arr = data if isinstance(data, list) else (data.get("models") or data.get("data") or [])
        out, seen = [], set()
        for m in arr:
            mid = m if isinstance(m, str) else (m.get("id") or m.get("slug") or m.get("name"))
            if isinstance(m, dict) and (m.get("hidden") or m.get("visibility") == "hide"):
                continue
            if mid and mid not in seen and not any(x in mid for x in ("reserve", "auto-review")):
                label = mid if isinstance(m, str) else m.get("display_name") or m.get("displayName") or mid
                out.append((mid, label))
                seen.add(mid)
        return out
    except Exception:
        return []

# Keep this deliberately narrower than a generic "limit" search: context-window,
# tool-output and file-size limits are turn-specific and must not move the whole
# conversation to another subscription. These phrases describe account capacity.
_USAGE_LIMIT_PHRASES = (
    "usage limit", "quota exceeded", "quota_exceeded", "rate limit exceeded",
    "rate_limit_exceeded", "too many requests", "insufficient quota",
    "insufficient_quota", "credits exhausted", "credit balance",
    "plan limit", "weekly limit", "five-hour limit", "5-hour limit",
    "you have no weighted tokens left", "http 429", "status 429",
)


def is_usage_exhausted(error: object) -> bool:
    """True only for an account-level usage/quota exhaustion error."""
    text = str(error or "").strip().lower()
    return any(phrase in text for phrase in _USAGE_LIMIT_PHRASES)


def available() -> tuple[bool, str]:
    """Is Codex usable right now? Checked before switching, so the failure is
    reported at the moment of the decision rather than on the next message."""
    if not Path(CODEX_BIN).exists():
        return False, f"codex CLI not found at {CODEX_BIN}"
    auth = Path.home() / ".codex/auth.json"
    if not auth.exists():
        return False, "not logged in — run: codex login"
    try:
        d = json.loads(auth.read_text())
    except Exception:
        return False, "~/.codex/auth.json is unreadable"
    if not ((d.get("tokens") or {}).get("access_token") or d.get("OPENAI_API_KEY")):
        return False, "no usable credential in ~/.codex/auth.json"
    mode = "ChatGPT subscription" if (d.get("tokens") or {}).get("access_token") else "API key"
    return True, mode


def thread_for(agent: str) -> str | None:
    return _threads.get(agent)


def clear_thread(agent: str) -> None:
    _threads.pop(agent, None)
    _persist()


def _argv(prompt: str, workdir: str, thread: str | None,
          model: str | None = None) -> list[str]:
    # Codex sandboxes by default and would stop to ask before writing. There is
    # no interactive terminal here, so an approval prompt just hangs the turn;
    # the bridge is already an authorised agent running as the owner.
    flags = ["--json", "--skip-git-repo-check", "--dangerously-bypass-approvals-and-sandbox"]
    mflag = ["-m", model] if model else []
    if thread:
        # resume restores cwd and context, but accepts a new model explicitly.
        return [CODEX_BIN, "exec", "resume", *flags, *mflag, thread, prompt]
    return [CODEX_BIN, "exec", *flags, *mflag, "--cd", workdir, prompt]


def _extract(stdout: str) -> tuple[str, str | None, dict, list[str]]:
    """Pull the reply, the thread id, usage and error messages out of the JSON
    event stream."""
    parts: list[str] = []
    errors: list[str] = []
    thread: str | None = None
    usage: dict = {}
    for line in stdout.splitlines():
        line = line.strip()
        if not line.startswith("{"):
            continue
        try:
            ev = json.loads(line)
        except Exception:
            continue
        t = ev.get("type")
        if t == "thread.started":
            thread = ev.get("thread_id") or thread
        elif t == "item.completed":
            item = ev.get("item") or {}
            if item.get("type") == "agent_message" and item.get("text"):
                parts.append(item["text"])
        elif t == "turn.completed":
            usage = ev.get("usage") or {}
        elif t == "turn.failed" and (ev.get("error") or {}).get("message"):
            errors.append(ev["error"]["message"])
            parts.append(f"⚠️ codex: {ev['error']['message']}")
        elif t == "error" and ev.get("message"):
            errors.append(ev["message"])
            parts.append(f"⚠️ codex: {ev['message']}")
    return "\n\n".join(parts).strip(), thread, usage, errors


async def run_turn(agent: str, prompt: str, workdir: str,
                   timeout: float = 900.0) -> tuple[str, dict]:
    """Run one turn. Returns (reply_text, usage). Raises on process failure."""
    thread = thread_for(agent)
    argv = _argv(prompt, workdir, thread, _models.get(agent) or default_model())
    proc = await asyncio.create_subprocess_exec(
        *argv,
        stdout=asyncio.subprocess.PIPE,
        stderr=asyncio.subprocess.PIPE,
        cwd=workdir,
        env={**os.environ, "PATH": os.environ.get("PATH", "") + os.pathsep + str(Path.home() / ".local/bin")},
    )
    try:
        out, err = await asyncio.wait_for(proc.communicate(), timeout=timeout)
    except asyncio.TimeoutError:
        proc.kill()
        await proc.wait()
        raise RuntimeError(f"codex timed out after {int(timeout)}s")

    text, new_thread, usage, errors = _extract(out.decode("utf-8", "replace"))
    if new_thread:
        _threads[agent] = new_thread
    elif thread:
        # A resumed turn does not re-announce the thread; keep the one we had.
        _threads[agent] = thread
    _persist()

    # A quota error arrives as an event, not a failed exit, so it used to come
    # back as an ordinary reply and the session never left Codex. Raise it so
    # feed() sees it and switches the session back to Claude.
    exhausted = [e for e in errors if is_usage_exhausted(e)]
    if exhausted:
        raise RuntimeError(f"codex usage exhausted: {exhausted[-1][:300]}")

    if errors:
        raise RuntimeError(f"codex turn failed: {errors[-1][:300]}")
    if proc.returncode != 0:
        tail = (err.decode("utf-8", "replace") or "").strip().splitlines()[-3:]
        raise RuntimeError(f"codex exited {proc.returncode}: {' / '.join(tail)[:300]}")
    return text or "(codex returned nothing)", usage
