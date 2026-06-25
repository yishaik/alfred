---
type: Module
title: fmt.py — markdown → Telegram HTML
description: Converts markdown to Telegram HTML, splits long messages under the 4000-char limit, and summarizes tool calls.
resource: tgbridge/fmt.py
tags: [module, formatting]
timestamp: 2026-06-17T00:00:00Z
---

# Responsibility
Pure formatting. Renders Claude's markdown into Telegram-safe HTML and keeps
messages within Telegram's limits.

# Key behaviors
- Renders tables, blockquotes, spoilers, underline.
- Splits messages respecting the 4000-char cap.
- Summarizes tool calls for compact display.

# Collaborators
Pure functions. Used by [outbox](/components/outbox.md) and [guards](/components/guards.md).
