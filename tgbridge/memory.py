"""Long-term memory for an agent — what it carries between sessions.

A session is ephemeral; the conversation scrolls away and a /clear or a crash
wipes the working context. Memory is the durable layer underneath: a small,
curated set of things worth remembering, persisted per agent and injected back
into every fresh session so the agent recalls them without anyone running
/find.

Items have a kind, which decides how they're treated:
  * pinned — the user explicitly said "remember this". Never decays, always
             injected. The backbone of "always know that X".
  * note   — the agent jotted something down itself (secretary notes, an
             observation). Subject to decay over time (see memory_decay, #11).
  * fact   — a neutral middle ground for imported/curated knowledge (#14).

This module is the store + rendering; the persistence wiring lives on the
manager, the user commands in handlers, and the agent-facing remember/forget
tools come later. The pure logic here (add/dedupe/remove/search/render) is
unit-tested.
"""

import time
from dataclasses import dataclass, field

KINDS = ("pinned", "note", "fact")

# How much memory may flow into a prompt, so recall never crowds out the task.
MAX_INJECT_ITEMS = 30
MAX_INJECT_CHARS = 2000


@dataclass
class MemoryItem:
    text: str
    kind: str = "fact"
    created: float = 0.0        # epoch seconds; 0 -> stamped on add
    last_used: float = 0.0      # last time it was recalled/injected
    tier: str = "full"          # full | summary (decay collapses full->summary)

    def to_dict(self) -> dict:
        return {"text": self.text, "kind": self.kind, "created": self.created,
                "last_used": self.last_used, "tier": self.tier}

    @classmethod
    def from_dict(cls, d: dict) -> "MemoryItem":
        return cls(text=d.get("text", ""), kind=d.get("kind", "fact"),
                   created=d.get("created", 0.0),
                   last_used=d.get("last_used", 0.0),
                   tier=d.get("tier", "full"))


class Memory:
    def __init__(self, items: list | None = None):
        self.items: list[MemoryItem] = items or []

    # -- mutation ------------------------------------------------------------ #
    def add(self, text: str, kind: str = "fact", now: float | None = None) -> MemoryItem | None:
        """Add an item, de-duplicating on (normalised text). Returns the item,
        or None for empty text. A repeat add refreshes the existing item's
        recency and upgrades a note to pinned if asked."""
        text = (text or "").strip()
        if not text:
            return None
        if kind not in KINDS:
            kind = "fact"
        now = time.time() if now is None else now
        key = text.lower()
        for it in self.items:
            if it.text.lower() == key:
                it.last_used = now
                if kind == "pinned":      # pinning an existing note sticks it
                    it.kind = "pinned"
                    it.tier = "full"
                return it
        item = MemoryItem(text=text, kind=kind, created=now, last_used=now)
        self.items.append(item)
        return item

    def remove(self, ref: str) -> str | None:
        """Forget an item by 1-based index ("3") or by case-insensitive
        substring. Returns the removed text, or None if nothing matched."""
        ref = (ref or "").strip()
        if not ref:
            return None
        if ref.lstrip("#").isdigit():
            i = int(ref.lstrip("#")) - 1
            if 0 <= i < len(self.items):
                return self.items.pop(i).text
            return None
        low = ref.lower()
        for i, it in enumerate(self.items):
            if low in it.text.lower():
                return self.items.pop(i).text
        return None

    # -- queries ------------------------------------------------------------- #
    def search(self, query: str) -> list[MemoryItem]:
        low = (query or "").lower().strip()
        if not low:
            return list(self.items)
        return [it for it in self.items if low in it.text.lower()]

    def _ordered(self) -> list[MemoryItem]:
        """Pinned first, then most-recently-used — the order both injection and
        the listing use."""
        return sorted(self.items,
                      key=lambda it: (it.kind != "pinned", -it.last_used))

    # -- rendering ----------------------------------------------------------- #
    def render_prompt(self, now: float | None = None) -> str:
        """A compact block injected into a fresh session so the agent recalls
        what matters. Empty when there's nothing to say. Marks the injected
        items as just-used so decay leaves recalled things alone."""
        if not self.items:
            return ""
        now = time.time() if now is None else now
        lines, used = [], 0
        for it in self._ordered()[:MAX_INJECT_ITEMS]:
            tag = "📌" if it.kind == "pinned" else "·"
            line = f"{tag} {it.text}"
            if used + len(line) > MAX_INJECT_CHARS:
                break
            lines.append(line)
            used += len(line)
            it.last_used = now
        if not lines:
            return ""
        return ("WHAT YOU REMEMBER (carried from earlier sessions — treat as "
                "background you already know, don't re-announce it):\n"
                + "\n".join(lines))

    def render_list(self) -> str:
        """Human-readable, numbered — for the /memory command."""
        if not self.items:
            return "🧠 nothing remembered yet. /remember <text> to pin something."
        out = [f"🧠 {len(self.items)} remembered:"]
        for i, it in enumerate(self._ordered(), 1):
            tag = "📌" if it.kind == "pinned" else ("📝" if it.kind == "note" else "•")
            out.append(f"{i}. {tag} {it.text}")
        return "\n".join(out)

    # -- persistence --------------------------------------------------------- #
    def to_list(self) -> list:
        return [it.to_dict() for it in self.items]

    @classmethod
    def from_list(cls, data: list | None) -> "Memory":
        return cls([MemoryItem.from_dict(d) for d in (data or [])])
