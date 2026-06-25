"""Long-term memory for an agent — what it carries between sessions.

A session is ephemeral; the conversation scrolls away and a /clear or a crash
wipes the working context. Memory is the durable layer underneath, injected
back into every fresh session so the agent recalls things without anyone
searching.

The store underneath is a per-agent **Napkin** knowledge vault (local-first
markdown, BM25 search, progressive disclosure) rather than a flat JSON list.
This module is a thin facade that keeps the public surface the rest of the
bridge already calls — add / remove / search / render_prompt / render_list /
decay / .items — so handlers, the MCP tools, the digest and the manager are
unaffected by what backs it.

Vault layout (state/kb/<agent>/):
  * NAPKIN.md         — the always-injected Level-0 context note. Pinned facts
                        ("remember this — always know X") live in a managed
                        block here and are injected verbatim every session.
  * notes/<slug>.md   — notes/facts the agent jotted down. NOT injected;
                        surfaced on demand via BM25 search (the recall tool).
  * .napkin/          — Napkin's own index metadata (like .git).

Item kinds decide treatment:
  * pinned — always injected (NAPKIN.md block). The backbone of "always know X".
  * note   — the agent's own observation; searched on demand, never injected.
  * fact   — imported/curated knowledge; treated like a note.

Pinned CRUD edits NAPKIN.md directly (plain file IO — fast, no subprocess).
Notes go through the napkin CLI (tgbridge.napkin_store). render_prompt is the
only hot path that shells out (for the TF-IDF overview map); it is cached and
invalidated on every write.
"""

import hashlib
import logging
import re
import time
from dataclasses import dataclass
from pathlib import Path

from . import napkin_store

log = logging.getLogger("bridge.memory")

KINDS = ("pinned", "note", "fact")

# How much pinned memory may flow into a prompt, so recall never crowds the task.
MAX_INJECT_ITEMS = 30
MAX_INJECT_CHARS = 2000

# Markers delimiting the managed pinned block inside NAPKIN.md.
PIN_START = "<!-- alfred:pinned:start -->"
PIN_END = "<!-- alfred:pinned:end -->"


@dataclass
class MemoryItem:
    """A single remembered thing, as surfaced by the facade. Lightweight — the
    durable form lives in the vault (NAPKIN.md bullet or notes/<slug>.md)."""
    text: str
    kind: str = "fact"          # pinned | note | fact
    file: str = ""              # vault-relative path (notes only; "" for pinned)
    created: float = 0.0        # epoch seconds; 0 -> stamped on add


def _slug(text: str) -> str:
    """A short, deterministic, filesystem-safe slug for a note. The trailing
    hash of the normalised text makes the same fact map to the same file, so a
    repeat `add` dedupes naturally (create-then-exists is treated as success)."""
    words = re.sub(r"[^\w\s-]", "", text.lower(), flags=re.UNICODE).split()
    stem = "-".join(words[:6])[:48] or "note"
    h = hashlib.sha1(text.strip().lower().encode("utf-8")).hexdigest()[:6]
    return f"{stem}-{h}"


class Memory:
    """An agent's long-term memory, backed by a Napkin vault at `vault`."""

    def __init__(self, vault: str):
        self.vault = str(vault)
        napkin_store.ensure_vault(self.vault)   # cheap if it already exists
        self._prompt_cache: str | None = None   # render_prompt result; None = stale

    @property
    def _napkin_md(self) -> Path:
        return Path(self.vault) / "NAPKIN.md"

    @property
    def _notes_dir(self) -> Path:
        return Path(self.vault) / "notes"

    # -- pinned block (direct NAPKIN.md IO) ---------------------------------- #
    def _read_text(self) -> str:
        try:
            return self._napkin_md.read_text(encoding="utf-8")
        except OSError:
            return ""

    def _read_pinned(self) -> list[str]:
        """The pinned bullets currently in NAPKIN.md's managed block."""
        text = self._read_text()
        if PIN_START not in text:
            return []
        block = text.split(PIN_START, 1)[1].split(PIN_END, 1)[0]
        out = []
        for line in block.splitlines():
            line = line.strip()
            if line.startswith("- "):
                out.append(line[2:].strip())
        return out

    def _write_pinned(self, bullets: list[str]) -> None:
        """Rewrite the managed pinned block, creating it (and NAPKIN.md) if
        absent. Everything outside the markers is preserved verbatim."""
        body = "\n".join(f"- {b}" for b in bullets)
        block = f"{PIN_START}\n## Pinned\n{body}\n{PIN_END}"
        text = self._read_text()
        if PIN_START in text and PIN_END in text:
            head, rest = text.split(PIN_START, 1)
            _, tail = rest.split(PIN_END, 1)
            text = head + block + tail
        else:
            sep = "" if text.endswith("\n") or not text else "\n\n"
            text = (text + sep + "\n" + block + "\n") if text else block + "\n"
        try:
            self._napkin_md.write_text(text, encoding="utf-8")
        except OSError:
            pass
        self._prompt_cache = None

    # -- mutation ------------------------------------------------------------ #
    def add(self, text: str, kind: str = "fact", now: float | None = None) -> MemoryItem | None:
        """Add an item. Pinned facts go to NAPKIN.md; notes/facts become vault
        files. De-duplicates on normalised text. Returns the item, or None for
        empty text."""
        text = (text or "").strip()
        if not text:
            return None
        if kind not in KINDS:
            kind = "fact"
        now = time.time() if now is None else now
        if kind == "pinned":
            bullets = self._read_pinned()
            if not any(b.lower() == text.lower() for b in bullets):
                bullets.append(text)
                self._write_pinned(bullets)
            return MemoryItem(text=text, kind="pinned", created=now)
        slug = _slug(text)
        path = f"notes/{slug}.md"
        try:
            path = napkin_store.create(self.vault, f"notes/{slug}", text) or path
        except napkin_store.NapkinError as e:
            # a duplicate slug is the expected, benign case ("already remembered").
            # Anything else (disk full, corrupt index) would otherwise silently
            # lose the note — surface it so the tool can report failure.
            if "already exist" not in str(e).lower():
                log.warning("memory add failed in %s: %s", self.vault, e)
                raise
        self._prompt_cache = None
        return MemoryItem(text=text, kind=kind, file=path, created=now)

    def remove(self, ref: str) -> str | None:
        """Forget an item by 1-based index (matching render_list order) or by
        case-insensitive substring. Returns the removed text, or None."""
        ref = (ref or "").strip()
        if not ref:
            return None
        items = self.items
        if ref.lstrip("#").isdigit():
            i = int(ref.lstrip("#")) - 1
            if 0 <= i < len(items):
                return self._delete(items[i])
            return None
        low = ref.lower()
        for it in items:
            if low in it.text.lower():
                return self._delete(it)
        return None

    def _delete(self, it: MemoryItem) -> str:
        if it.kind == "pinned":
            kept = [b for b in self._read_pinned() if b.lower() != it.text.lower()]
            self._write_pinned(kept)
        else:
            try:
                napkin_store.delete(self.vault, it.file or it.text)
            except napkin_store.NapkinError:
                pass
        self._prompt_cache = None
        return it.text

    # -- queries ------------------------------------------------------------- #
    def search(self, query: str, now: float | None = None) -> list[MemoryItem]:
        """BM25 search over notes (+ substring over pinned). Empty query returns
        everything, matching the old behaviour."""
        low = (query or "").strip()
        if not low:
            return self.items
        out: list[MemoryItem] = []
        for b in self._read_pinned():               # pinned first, always matchable
            if low.lower() in b.lower():
                out.append(MemoryItem(text=b, kind="pinned"))
        try:
            for r in napkin_store.search(self.vault, low):
                # napkin returns Windows-style paths; normalise and surface ONLY
                # user-authored notes. The vault ships with template scaffolding
                # (references/_about.md, etc.) that BM25 would otherwise return
                # as a fake "remembered" fact — add() only ever writes notes/.
                fpath = (r.get("file", "") or "").replace("\\", "/")
                if not fpath.startswith("notes/"):
                    continue
                snip = " ".join(s.get("text", "") for s in r.get("snippets", []))
                out.append(MemoryItem(text=(snip.strip() or fpath),
                                      kind="note", file=fpath))
        except napkin_store.NapkinError:
            pass
        return out

    # -- decay (superseded) -------------------------------------------------- #
    def decay(self, now: float | None = None) -> int:
        """No-op: Napkin's BM25 search already ranks by recency, and only pinned
        facts are injected, so there is nothing to fade. Kept so the manager's
        daily maintenance call stays valid."""
        return 0

    # -- listing / rendering ------------------------------------------------- #
    @property
    def items(self) -> list[MemoryItem]:
        """Everything remembered: pinned bullets then notes (by name). Reads the
        vault from disk, so callers should treat it as a snapshot, not a live
        list."""
        out = [MemoryItem(text=b, kind="pinned") for b in self._read_pinned()]
        if self._notes_dir.is_dir():
            for p in sorted(self._notes_dir.glob("*.md")):
                try:
                    txt = p.read_text(encoding="utf-8").strip()
                except OSError:
                    txt = p.stem
                out.append(MemoryItem(text=txt or p.stem, kind="note",
                                      file=f"notes/{p.name}"))
        return out

    def render_prompt(self, now: float | None = None) -> str:
        """Search-first injection (progressive disclosure). A fresh session gets
        only the always-on layer: pinned facts in full, plus Napkin's keyword
        map of the rest so the agent knows what it can `recall`. The bulk of the
        vault is pulled on demand, never dumped. Cached; invalidated on write."""
        if self._prompt_cache is not None:
            return self._prompt_cache
        blocks: list[str] = []
        pinned = self._read_pinned()
        if pinned:
            lines, used = [], 0
            for b in pinned[:MAX_INJECT_ITEMS]:
                line = f"📌 {b}"
                if used + len(line) > MAX_INJECT_CHARS:
                    break
                lines.append(line)
                used += len(line)
            blocks.append(
                "WHAT YOU REMEMBER (carried from earlier sessions — treat as "
                "background you already know, don't re-announce it):\n"
                + "\n".join(lines))
        try:
            overview = napkin_store.overview(self.vault).get("overview", [])
        except napkin_store.NapkinError:
            overview = []
        folders = [o for o in overview if o.get("notes")]
        if folders:
            map_lines = []
            for o in folders:
                kws = ", ".join(o.get("keywords", [])[:8])
                map_lines.append(f"• {o.get('path', '/')} ({o['notes']}): {kws}")
            blocks.append(
                "LONG-TERM MEMORY MAP (your knowledge vault — search it with the "
                "`recall` tool and read a file with `kb_read` whenever a topic "
                "might already be known, before asking the user to repeat "
                "themselves):\n" + "\n".join(map_lines))
        self._prompt_cache = "\n\n".join(blocks)
        return self._prompt_cache

    def render_list(self) -> str:
        """Human-readable, numbered — for the /memory command."""
        items = self.items
        if not items:
            return "🧠 nothing remembered yet. /remember <text> to pin something."
        out = [f"🧠 {len(items)} remembered:"]
        for i, it in enumerate(items, 1):
            tag = "📌" if it.kind == "pinned" else "📝"
            out.append(f"{i}. {tag} {it.text[:200]}")
        return "\n".join(out)
