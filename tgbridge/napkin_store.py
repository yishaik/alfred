"""Thin wrapper over the `napkin` CLI (napkin-ai) — a local-first, file-based
knowledge vault with BM25 search and progressive disclosure.

Each agent gets its own vault directory (state/kb/<agent>/). The vault is just
plain markdown on disk; a `.napkin/` metadata folder (like `.git`) holds the
search index. We drive the CLI's `--json` mode by invoking the package's JS
entry point through `node` directly, which sidesteps the `.cmd`/`.ps1` shims on
Windows that `subprocess` can't exec.

Every call here spawns a short-lived `node` process (~1-2s cold), so callers on
the asyncio event loop must NOT call these in a hot path without caching — see
Memory.render_prompt, which caches the rendered block and refreshes on writes.
"""

import json
import logging
import os
import shutil
import subprocess
from functools import lru_cache
from pathlib import Path

log = logging.getLogger("bridge.napkin")

# A cold `node` start plus a BM25 reindex can take a couple of seconds; keep the
# ceiling generous but bounded so a wedged call can't hang a turn forever.
TIMEOUT_S = 30


class NapkinError(RuntimeError):
    """Any failure invoking the napkin CLI (missing binary, non-zero exit,
    timeout, or unparseable output)."""


@lru_cache(maxsize=1)
def _base_cmd() -> list[str] | None:
    """Resolve how to invoke napkin, once. Prefers `node <main.js>` because the
    PATH entry on Windows is a `.cmd` shim that subprocess can't launch
    directly. Order: explicit NAPKIN_BIN override, global npm install, then a
    bare `napkin` on PATH as a last resort. Returns None if nothing is found."""
    override = os.environ.get("NAPKIN_BIN")
    if override:
        return ["node", override] if override.endswith(".js") else [override]
    # global npm install — Windows (%APPDATA%\npm) then common unix prefixes
    candidates = []
    appdata = os.environ.get("APPDATA")
    if appdata:
        candidates.append(Path(appdata) / "npm" / "node_modules")
    candidates += [Path("/usr/local/lib") / "node_modules",
                   Path("/usr/lib") / "node_modules"]
    for nm in candidates:
        mj = nm / "napkin-ai" / "dist" / "main.js"
        if mj.exists():
            return ["node", str(mj)]
    w = shutil.which("napkin")
    if w:
        return [w]
    return None


def available() -> bool:
    """Whether the napkin CLI can be located at all."""
    return _base_cmd() is not None


def _run(vault: str, *args: str) -> dict:
    """Run one napkin command against `vault` and return its parsed JSON dict.
    Raises NapkinError on any failure so callers can degrade gracefully."""
    base = _base_cmd()
    if base is None:
        raise NapkinError("napkin CLI not found (npm i -g napkin-ai)")
    cmd = [*base, "--json", "--vault", str(vault), *args]
    # Run *inside* the vault so napkin's cwd auto-detection agrees with --vault:
    # it walks up from cwd to the nearest `.napkin`, which must be the vault's
    # own. Otherwise a stray `.napkin` in an ancestor silently captures every
    # vault into one shared store (per-agent isolation breaks).
    try:
        r = subprocess.run(cmd, capture_output=True, text=True,
                           timeout=TIMEOUT_S, cwd=str(vault))
    except subprocess.TimeoutExpired:
        raise NapkinError(f"napkin timed out on: {' '.join(args[:2])}")
    except OSError as e:
        raise NapkinError(f"could not run napkin: {e}")
    if r.returncode != 0:
        raise NapkinError(
            f"napkin {list(args[:2])} exited {r.returncode}: "
            f"{(r.stderr or r.stdout or '').strip()[:200]}")
    out = (r.stdout or "").strip()
    if not out:
        return {}
    try:
        return json.loads(out)
    except json.JSONDecodeError:
        raise NapkinError(f"napkin gave non-JSON output: {out[:200]}")


def ensure_vault(vault: str, template: str = "personal") -> None:
    """Create the vault if its `.napkin` metadata dir is missing. Idempotent and
    cheap when the vault already exists (no subprocess)."""
    if (Path(vault) / ".napkin").exists():
        return
    Path(vault).mkdir(parents=True, exist_ok=True)
    _run(vault, "init", "--template", template)


def overview(vault: str) -> dict:
    """The vault map. Returns {"context": <NAPKIN.md text>,
    "overview": [{"path", "notes", "keywords": [...], "tags": [...]}, ...]}."""
    return _run(vault, "overview")


def search(vault: str, query: str) -> list[dict]:
    """BM25 + backlinks + recency. Returns a list of
    {"file", "links", "modified", "snippets": [{"line", "text"}, ...]}."""
    if not (query or "").strip():
        return []
    return _run(vault, "search", query).get("results", [])


def read(vault: str, name: str) -> str:
    """Full text of one file (name with or without the .md extension)."""
    return _run(vault, "read", name).get("content", "")


def create(vault: str, name: str, content: str,
           template: str | None = None) -> str:
    """Create a note file; returns its vault-relative path. Raises if it already
    exists (callers treat that as 'already stored')."""
    args = ["create", name, "--content", content]
    if template:
        args += ["--template", template]
    return _run(vault, *args).get("path", "")


def append(vault: str, name: str, text: str) -> bool:
    return _run(vault, "append", name, text).get("appended", False)


def delete(vault: str, name: str) -> bool:
    """Delete a file (to the vault trash, not permanent)."""
    return _run(vault, "delete", name).get("deleted", False)
