---
type: Module
title: session.py — the Claude Agent SDK client
description: One long-lived Agent SDK client per chat/topic; queues turns, recovers from crashes, applies guards, personality, and rate limits.
resource: tgbridge/session.py
tags: [module, core, claude]
timestamp: 2026-06-17T00:00:00Z
---

# Responsibility
Wraps a long-lived Claude Agent SDK client bound to one Telegram route. The unit
of conversation: one per private chat or forum topic.

# Key behaviors
- Queues user turns; the queue survives restarts and is re-fed.
- Crash recovery via [backoff](/components/ratelimit.md); fresh session after 3 fast crashes.
- Budgets non-human turns; long-turn watchdog for hangs.
- Applies [guards](/components/guards.md), [soul](/components/personality/soul.md) +
  [mood](/components/personality/mood.md), and exposes [bridge tools](/components/bridgetools.md).

# Collaborators
[bridgetools](/components/bridgetools.md) · [guards](/components/guards.md) ·
[markers](/components/markers.md) · [voice](/components/voice.md) ·
[outbox](/components/outbox.md) · [soul](/components/personality/soul.md) ·
[mood](/components/personality/mood.md) · [ratelimit](/components/ratelimit.md)
