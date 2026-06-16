"""A tiny Kanban to-do list — the first Alfred mini-app (#20).

One list for the user (the bridge is single-user), persisted across sessions.
Items move through three columns — todo -> doing -> done — and render as a
compact phone-friendly board. The logic here is pure and unit-tested; the
manager owns persistence and the /todo command drives it.
"""

import time
from dataclasses import dataclass

STATUSES = ("todo", "doing", "done")
_COLUMNS = [("todo", "📋 To do"), ("doing", "🔄 Doing"), ("done", "✅ Done")]


@dataclass
class Todo:
    id: int
    text: str
    status: str = "todo"
    created: float = 0.0

    def to_dict(self) -> dict:
        return {"id": self.id, "text": self.text, "status": self.status,
                "created": self.created}

    @classmethod
    def from_dict(cls, d: dict) -> "Todo":
        return cls(id=d.get("id", 0), text=d.get("text", ""),
                   status=d.get("status", "todo"), created=d.get("created", 0.0))


class TodoList:
    def __init__(self, items: list | None = None, seq: int = 0):
        self.items: list[Todo] = items or []
        self.seq = seq

    # -- mutation ------------------------------------------------------------ #
    def add(self, text: str, now: float | None = None) -> Todo | None:
        text = (text or "").strip()
        if not text:
            return None
        self.seq += 1
        item = Todo(id=self.seq, text=text,
                    created=time.time() if now is None else now)
        self.items.append(item)
        return item

    def _get(self, tid) -> Todo | None:
        try:
            tid = int(str(tid).lstrip("#"))
        except (ValueError, TypeError):
            return None
        return next((t for t in self.items if t.id == tid), None)

    def set_status(self, tid, status: str) -> Todo | None:
        if status not in STATUSES:
            return None
        t = self._get(tid)
        if t:
            t.status = status
        return t

    def remove(self, tid) -> Todo | None:
        t = self._get(tid)
        if t:
            self.items.remove(t)
        return t

    def clear_done(self) -> int:
        before = len(self.items)
        self.items = [t for t in self.items if t.status != "done"]
        return before - len(self.items)

    # -- rendering ----------------------------------------------------------- #
    def render(self) -> str:
        if not self.items:
            return "🗂 no tasks. /todo add <text> to start one."
        out = []
        for status, header in _COLUMNS:
            rows = [t for t in self.items if t.status == status]
            if not rows:
                continue
            out.append(f"{header} ({len(rows)})")
            for t in rows:
                text = f"~{t.text}~" if status == "done" else t.text
                out.append(f"  #{t.id} {text}")
        return "\n".join(out)

    # -- persistence --------------------------------------------------------- #
    def to_dict(self) -> dict:
        return {"seq": self.seq, "items": [t.to_dict() for t in self.items]}

    @classmethod
    def from_dict(cls, d: dict | None) -> "TodoList":
        d = d or {}
        return cls(items=[Todo.from_dict(x) for x in d.get("items", [])],
                   seq=d.get("seq", 0))
