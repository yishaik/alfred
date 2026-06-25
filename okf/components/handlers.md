---
type: Module
title: handlers.py — Telegram input
description: Telegram handlers for commands, button callbacks, media uploads, and voice transcription; routes messages to sessions.
resource: tgbridge/handlers.py
tags: [module, telegram, io]
timestamp: 2026-06-17T00:00:00Z
---

# Responsibility
The inbound edge. Processes bridge commands, button callbacks, media, and voice,
and hands turns to the right [session](/components/session.md) via the
[manager](/components/manager.md).

# Key behaviors
- Bridge commands: `/panel`, `/status`, `/interrupt`, `/agents`, `/jobs`, `/todo`, … (see [commands](/operations/commands.md)).
- Button callbacks (approvals, quick replies, queue control).
- Media uploads to `<workdir>\inbox\`; voice via [voice](/components/voice.md).

# Collaborators
[manager](/components/manager.md) · [config](/components/config.md) ·
[session](/components/session.md) · [voice](/components/voice.md) ·
[metrics](/components/ops/metrics.md)
