from dataclasses import dataclass

from tgbridge.memory_dreaming import (_apply_plan, _extract_json,
                                      _normalize_plan)


@dataclass
class Item:
    text: str
    kind: str = "note"


class FakeMemory:
    def __init__(self, items):
        self._items = list(items)

    @property
    def items(self):
        return list(self._items)

    def add(self, text, kind="note"):
        item = Item(text, kind)
        self._items.append(item)
        return item

    def remove(self, ref):
        if str(ref).isdigit():
            idx = int(ref) - 1
            if 0 <= idx < len(self._items):
                return self._items.pop(idx).text
            return None
        low = ref.lower()
        for idx, item in enumerate(self._items):
            if low in item.text.lower():
                return self._items.pop(idx).text
        return None


def test_extract_json_from_fence():
    assert _extract_json('```json\n{"upserts": [], "deletes": []}\n```') == {
        "upserts": [], "deletes": []}


def test_normalize_rejects_auto_pin_and_secrets():
    plan = _normalize_plan({
        "upserts": [
            {"text": "The user prefers concise answers", "kind": "pinned"},
            {"text": "api_key=sk_abcdefghijklmnopqrstuvwxyz", "kind": "note"},
        ]
    })
    assert plan["upserts"] == [{
        "text": "The user prefers concise answers",
        "kind": "note",
        "reason": "",
    }]


def test_apply_protects_pinned_and_ambiguous_deletes():
    mem = FakeMemory([
        Item("User lives in Tel Aviv", "pinned"),
        Item("Trip to Rome is planned for July", "note"),
        Item("Trip to Rome may happen in July", "note"),
    ])
    plan = _normalize_plan({
        "deletes": [
            {"match": "User lives in Tel Aviv"},
            {"match": "Trip to Rome"},
        ],
        "upserts": [{"text": "User prefers quiet hotels", "kind": "note"}],
    })
    result = _apply_plan(mem, plan)
    assert result["removed"] == []
    assert len(result["skipped"]) == 2
    assert result["added"] == ["User prefers quiet hotels"]
    assert any(item.kind == "pinned" for item in mem.items)


def test_delete_never_falls_through_to_pinned_substring():
    mem = FakeMemory([
        Item("User lives in Tel Aviv", "pinned"),
        Item("User lives in Tel Aviv during July", "note"),
    ])
    plan = _normalize_plan({
        "deletes": [{"match": "User lives in Tel Aviv"}],
    })
    result = _apply_plan(mem, plan)
    assert result["removed"] == []
    assert len(result["skipped"]) == 1
    assert [item.kind for item in mem.items] == ["pinned", "note"]


def test_apply_unique_non_pinned_replacement():
    mem = FakeMemory([Item("Project Alpha launches on 2026-07-01", "note")])
    plan = _normalize_plan({
        "deletes": [{"match": "Project Alpha launches"}],
        "upserts": [{"text": "Project Alpha launched in July 2026", "kind": "note"}],
    })
    result = _apply_plan(mem, plan)
    assert result["removed"] == ["Project Alpha launches on 2026-07-01"]
    assert result["added"] == ["Project Alpha launched in July 2026"]
