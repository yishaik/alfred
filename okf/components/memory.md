---
type: Module
title: memory.py — long-term agent memory
description: Pinned/note/fact items that survive restarts, plus nightly synthesis that keeps searchable memory current.
resource: tgbridge/memory.py
tags: [module, memory]
timestamp: 2026-07-20T00:00:00Z
---

# Responsibility
Per-agent long-term memory, surfaced into every [session](/components/session.md)
and editable via the `mcp__bridge__remember/forget/recall` [tools](/components/bridgetools.md).

# Key behaviors
- Item kinds: pinned, note, fact — persisted across restarts.
- Pinned items are always injected and may only be changed explicitly.
- Notes/facts are stored in the Napkin vault and recalled with BM25 search.
- Nightly memory dreaming compares recent turns with the vault, merges durable
  context, and retires uniquely identified stale non-pinned notes.
- The dreaming model has no tools; its JSON plan is validated locally before
  any mutation, and changes are logged under `state/memory-dreaming-log.jsonl`.

# Collaborators
Persisted by the [manager](/components/manager.md), consolidated by
[`memory_dreaming.py`](/components/memory-dreaming.md), and complemented by the
[contacts](/components/mini-apps/contacts.md) book.
