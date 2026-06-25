---
type: Module
title: memory.py — long-term agent memory
description: Pinned/note/fact items that survive restarts, are injected into every session, and decay over time.
resource: tgbridge/memory.py
tags: [module, memory]
timestamp: 2026-06-17T00:00:00Z
---

# Responsibility
Per-agent long-term memory, surfaced into every [session](/components/session.md)
and editable via the `mcp__bridge__remember/forget/recall` [tools](/components/bridgetools.md).

# Key behaviors
- Item kinds: pinned, note, fact — persisted across restarts.
- Decay: old notes collapse to summaries, then drop from injection.
- Pinned items are exempt from decay.

# Collaborators
Pure logic; persisted by the [manager](/components/manager.md). Complemented by the
[contacts](/components/mini-apps/contacts.md) book.
