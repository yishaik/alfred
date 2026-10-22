"""Antigravity as an alternative agent backend, alongside Claude and Codex.

Purpose:  Run a turn through Google's Antigravity instead of Claude/Codex.
Inputs:   A prompt, the agent's stored conversation id, the working directory.
Outputs:  The reply text; conversation id + transcript persisted for continuity.
Key fns:  available(), run_turn(), is_usage_exhausted(), thread_for/clear_thread
          — the same shape codex_engine exposes, so session.py dispatches to
          either the same way.
Deps:     `agy.exe` (the Antigravity CLI) + pywinpty, plus the tested ConPTY
          driver from the sibling "agy" bridge (agybridge.agycli), imported by
          path. Windows-only (ConPTY).

Same trade-offs as codex mode: the bridge's own tools (send_file, remember,
schedule…) are unavailable, no guard hooks, and replies arrive whole rather
than streaming. Antigravity re-prints the whole transcript when resuming a
conversation, so we strip the known prefix to return just the new tail.
"""
from __future__ import annotations

import asyncio
import logging
import os
import shutil
import subprocess
import sys
import tempfile
from pathlib import Path

from .config import STATE_DIR, load_json, save_json

log = logging.getLogger("bridge.antigravity")

AGY_ROOT = os.environ.get("BRIDGE_AGY_ROOT", r"D:\Projects\agy")
AGY_EXE = (os.environ.get("BRIDGE_AGY_EXE", "") or shutil.which("agy")
           or str(Path.home() / "AppData/Local/agy/bin/agy.exe"))
_TMP = Path(tempfile.gettempdir()) / "bridge-antigravity"

# One conversation per agent (+ last full transcript, to strip the replayed
# history on resume) so /engine antigravity continues where it left off.
_STATE_FILE = STATE_DIR / "antigravity-threads.json"
_state: dict[str, dict] = load_json(_STATE_FILE, {})


def _persist():
    try:
        save_json(_STATE_FILE, _state)
    except OSError:
        log.exception("could not persist Antigravity thread state")

_USAGE_LIMIT_PHRASES = (
    "usage limit", "quota exceeded", "quota_exceeded", "rate limit exceeded",
    "rate_limit_exceeded", "too many requests", "insufficient quota",
    "plan limit", "weekly limit", "http 429", "status 429", "credits exhausted",
)


def is_usage_exhausted(error: object) -> bool:
    text = str(error or "").strip().lower()
    return any(p in text for p in _USAGE_LIMIT_PHRASES)


def _load_driver():
    """Import the tested agy ConPTY driver from the sibling project by path."""
    if AGY_ROOT not in sys.path:
        sys.path.insert(0, AGY_ROOT)
    from agybridge.agycli import AgyRunner, TurnState, strip_known_prefix
    return AgyRunner, TurnState, strip_known_prefix


def available() -> tuple[bool, str]:
    """Is Antigravity usable right now? Checked before switching."""
    if os.name != "nt":
        return False, "antigravity engine is Windows-only (ConPTY)"
    if not Path(AGY_EXE).exists():
        return False, f"agy.exe not found at {AGY_EXE}"
    try:
        import winpty  # noqa: F401
    except Exception:
        return False, "pywinpty not installed (uv pip install pywinpty)"
    if not (Path(AGY_ROOT) / "agybridge" / "agycli.py").exists():
        return False, f"agy driver not found under {AGY_ROOT}"
    return True, "agy.exe (Antigravity CLI)"


def thread_for(agent: str) -> str | None:
    return (_state.get(agent) or {}).get("conv")


def clear_thread(agent: str) -> None:
    _state.pop(agent, None)
    _persist()


def get_model(agent: str) -> str | None:
    return (_state.get(agent) or {}).get("model")


def set_model(agent: str, model: str | None) -> None:
    st = _state.setdefault(agent, {"conv": None, "transcript": "", "model": None})
    st["model"] = model or None
    # A conversation is tied to the model it started on; reset so the new model
    # takes effect on the next turn.
    st["conv"] = None
    st["transcript"] = ""
    _persist()


_models_cache: list[tuple[str, str]] = []


def list_models(refresh: bool = False) -> list[tuple[str, str]]:
    """Antigravity's available models via `agy models` (cached). Rows are
    'id\\tLabel'; the leading 'Fetching…' line has no tab and is skipped."""
    global _models_cache
    if _models_cache and not refresh:
        return _models_cache
    try:
        out = subprocess.run([AGY_EXE, "models"], capture_output=True,
                             text=True, timeout=40).stdout
        rows = []
        for line in out.splitlines():
            if "\t" in line:
                mid, label = line.split("\t", 1)
                rows.append((mid.strip(), label.strip()))
        _models_cache = rows
        return rows
    except Exception:
        return []


async def run_turn(agent: str, prompt: str, workdir: str,
                   timeout: float = 1800.0) -> tuple[str, dict]:
    """Run one turn. Returns (reply_text, usage). Raises on process failure."""
    AgyRunner, TurnState, strip_known_prefix = _load_driver()
    st = _state.setdefault(agent, {"conv": None, "transcript": "", "model": None})
    runner = AgyRunner(exe=AGY_EXE, workdir=workdir, tmp_dir=_TMP,
                       model=st.get("model") or "", skip_permissions=True,
                       timeout=int(timeout))
    state = TurnState()
    result = await asyncio.to_thread(runner.run_turn, prompt, st["conv"], state)
    if getattr(result, "error", None) and not result.text:
        raise RuntimeError(result.error)
    tail = strip_known_prefix(result.text, st["transcript"])
    if result.conversation_id:
        st["conv"] = result.conversation_id
    st["transcript"] = result.text
    _persist()
    return tail or "(antigravity returned nothing)", {}
