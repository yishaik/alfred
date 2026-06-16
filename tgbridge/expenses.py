"""A pocket expense tracker mini-app (#24).

"/expense add 200 #food groceries" logs a spend; "/expense" shows the month's
total broken down by #category. One ledger for the user, persisted. Pure logic
(parse, total, breakdown, render) is unit-tested; the manager persists it.
"""

import time
from collections import Counter
from dataclasses import dataclass
from datetime import date


def parse_amount_note(text: str) -> tuple[float | None, str, str]:
    """'200 #food lunch with X' -> (200.0, 'food', 'lunch with X'). The amount
    is the first token; an optional #tag anywhere is the category."""
    text = (text or "").strip()
    if not text:
        return None, "", ""
    parts = text.split()
    try:
        amount = float(parts[0].lstrip("$₪").replace(",", ""))
    except ValueError:
        return None, "", ""
    rest = parts[1:]
    category = ""
    note_words = []
    for w in rest:
        if w.startswith("#") and len(w) > 1 and not category:
            category = w[1:]
        else:
            note_words.append(w)
    return amount, category, " ".join(note_words)


@dataclass
class Expense:
    id: int
    amount: float
    category: str = ""
    note: str = ""
    created: float = 0.0
    month: str = ""        # YYYY-MM, stamped on add for stable grouping

    def to_dict(self) -> dict:
        return {"id": self.id, "amount": self.amount, "category": self.category,
                "note": self.note, "created": self.created, "month": self.month}

    @classmethod
    def from_dict(cls, d: dict) -> "Expense":
        return cls(id=d.get("id", 0), amount=d.get("amount", 0.0),
                   category=d.get("category", ""), note=d.get("note", ""),
                   created=d.get("created", 0.0), month=d.get("month", ""))


class Ledger:
    def __init__(self, items: list | None = None, seq: int = 0):
        self.items: list[Expense] = items or []
        self.seq = seq

    def add(self, amount: float, category: str = "", note: str = "",
            now: float | None = None, month: str | None = None) -> Expense:
        self.seq += 1
        e = Expense(id=self.seq, amount=round(float(amount), 2),
                    category=category, note=note,
                    created=time.time() if now is None else now,
                    month=month or date.today().strftime("%Y-%m"))
        self.items.append(e)
        return e

    def remove(self, eid) -> Expense | None:
        try:
            eid = int(str(eid).lstrip("#"))
        except (ValueError, TypeError):
            return None
        e = next((x for x in self.items if x.id == eid), None)
        if e:
            self.items.remove(e)
        return e

    def _for_month(self, month: str) -> list[Expense]:
        return [e for e in self.items if e.month == month]

    def total(self, month: str) -> float:
        return round(sum(e.amount for e in self._for_month(month)), 2)

    def by_category(self, month: str) -> Counter:
        c: Counter = Counter()
        for e in self._for_month(month):
            c[e.category or "·other"] += e.amount
        return c

    def render(self, month: str | None = None) -> str:
        month = month or date.today().strftime("%Y-%m")
        rows = self._for_month(month)
        if not rows:
            return (f"💸 {month}: nothing logged.\n"
                    "/expense add <amount> [#category] [note]")
        out = [f"💸 {month}: ${self.total(month):,.2f}"]
        for cat, amt in self.by_category(month).most_common():
            out.append(f"  {cat} ${amt:,.2f}")
        out.append("recent:")
        for e in rows[-5:]:
            tag = f" #{e.category}" if e.category else ""
            out.append(f"  #{e.id} ${e.amount:,.2f}{tag} {e.note}".rstrip())
        return "\n".join(out)

    def to_dict(self) -> dict:
        return {"seq": self.seq, "items": [e.to_dict() for e in self.items]}

    @classmethod
    def from_dict(cls, d: dict | None) -> "Ledger":
        d = d or {}
        return cls(items=[Expense.from_dict(x) for x in d.get("items", [])],
                   seq=d.get("seq", 0))
