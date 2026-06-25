---
type: Module
title: outbox.py — delivery queue
description: Per-route delivery queue with batching, throttling, streaming-draft edits, and HTML→plain-text fallback.
resource: tgbridge/outbox.py
tags: [module, io, telegram]
timestamp: 2026-06-17T00:00:00Z
---

# Responsibility
The outbound edge. Queues text, files, keyboards, and streaming drafts per route
and delivers them within Telegram's rate limits.

# Key behaviors
- Live streaming: one message edited in place as Claude types.
- Throttles sends (≥1.05 s apart per chat; draft edits ≥1.5 s).
- Mutes during `/collect` to gather assistant output.
- Falls back to plain text if Telegram rejects the HTML (via [fmt](/components/fmt.md)).

# Collaborators
[fmt](/components/fmt.md)
