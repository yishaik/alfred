"""A lightweight contact book mini-app (#25).

"/contact add Dana | plumber, fixed the boiler, 050-..." remembers a person and
how they relate to you; "/contact find dana" looks them up. Complements the
agent's own memory (pinned facts) with a structured, browsable people list.
Pure logic is unit-tested; the manager persists it.
"""

import time
from dataclasses import dataclass


@dataclass
class Contact:
    id: int
    name: str
    info: str = ""
    created: float = 0.0

    def to_dict(self) -> dict:
        return {"id": self.id, "name": self.name, "info": self.info,
                "created": self.created}

    @classmethod
    def from_dict(cls, d: dict) -> "Contact":
        return cls(id=d.get("id", 0), name=d.get("name", ""),
                   info=d.get("info", ""), created=d.get("created", 0.0))


class ContactBook:
    def __init__(self, items: list | None = None, seq: int = 0):
        self.items: list[Contact] = items or []
        self.seq = seq

    def add(self, name: str, info: str = "", now: float | None = None) -> Contact | None:
        name = (name or "").strip()
        if not name:
            return None
        self.seq += 1
        c = Contact(id=self.seq, name=name, info=(info or "").strip(),
                    created=time.time() if now is None else now)
        self.items.append(c)
        return c

    def remove(self, cid) -> Contact | None:
        try:
            cid = int(str(cid).lstrip("#"))
        except (ValueError, TypeError):
            return None
        c = next((x for x in self.items if x.id == cid), None)
        if c:
            self.items.remove(c)
        return c

    def find(self, query: str) -> list[Contact]:
        q = (query or "").lower().strip()
        if not q:
            return list(self.items)
        return [c for c in self.items
                if q in c.name.lower() or q in c.info.lower()]

    def render(self, matches: list | None = None) -> str:
        rows = self.items if matches is None else matches
        if not rows:
            return ("📇 no contacts yet. /contact add <name> | <details>"
                    if matches is None else "📇 no matches.")
        out = [f"📇 contacts ({len(rows)}):"]
        for c in sorted(rows, key=lambda x: x.name.lower()):
            out.append(f"  #{c.id} {c.name}" + (f" — {c.info}" if c.info else ""))
        return "\n".join(out)

    def to_dict(self) -> dict:
        return {"seq": self.seq, "items": [c.to_dict() for c in self.items]}

    @classmethod
    def from_dict(cls, d: dict | None) -> "ContactBook":
        d = d or {}
        return cls(items=[Contact.from_dict(x) for x in d.get("items", [])],
                   seq=d.get("seq", 0))
