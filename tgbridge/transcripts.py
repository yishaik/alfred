"""Full-text search over local session transcripts (~/.claude/projects).

Purpose:  Lets the bridge search past conversation history by keyword.
Inputs:   query string; reads JSONL transcript files from Claude's project dirs.
Outputs:  List of matching snippets with session path + turn index.
Key fns:  search_transcripts(query, limit) -> list[dict].
Deps:     claude_agent_sdk.project_key_for_directory; no bridge-internal deps.
Note:     Reads Claude Code's own transcript format (content blocks as JSONL).
Updated:  2026-07-12
"""

import json
import logging
from pathlib import Path

from claude_agent_sdk import project_key_for_directory

log = logging.getLogger("bridge.transcripts")


def _block_text(content) -> str:
    if isinstance(content, str):
        return content
    if isinstance(content, list):
        return " ".join(b.get("text", "") for b in content
                        if isinstance(b, dict) and b.get("type") == "text")
    return ""


def search_transcripts(workdir: str, query: str, max_files: int = 20,
                       max_hits: int = 8) -> list[tuple[str, str]]:
    """Return [(session_id, snippet)] newest-first, one hit per session."""
    q = query.lower()
    try:
        proj = Path.home() / ".claude" / "projects" / project_key_for_directory(workdir)
        files = sorted(proj.glob("*.jsonl"),
                       key=lambda p: p.stat().st_mtime, reverse=True)[:max_files]
    except Exception as e:
        log.warning("transcript dir unavailable: %s", e)
        return []
    hits: list[tuple[str, str]] = []
    for f in files:
        try:
            with f.open(encoding="utf-8", errors="replace") as fh:
                for raw in fh:
                    if q not in raw.lower():
                        continue
                    try:
                        obj = json.loads(raw)
                    except json.JSONDecodeError:
                        continue
                    if obj.get("type") not in ("user", "assistant"):
                        continue
                    text = _block_text((obj.get("message") or {}).get("content"))
                    idx = text.lower().find(q)
                    if idx < 0:
                        continue
                    snippet = text[max(0, idx - 40):idx + len(query) + 60] \
                        .replace("\n", " ").strip()
                    hits.append((f.stem, f"…{snippet}…"))
                    break               # one hit per session file
        except OSError:
            continue
        if len(hits) >= max_hits:
            break
    return hits
