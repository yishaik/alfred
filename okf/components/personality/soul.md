---
type: Module
title: soul.py — character sheet
description: Structured persona (name, emoji, role, tone, values, quirks) injected into every turn's system prompt.
resource: tgbridge/soul.py
tags: [module, personality]
timestamp: 2026-06-17T00:00:00Z
---

# Responsibility
The stable character of an agent. Renders a system-prompt block injected into
every turn, and a human-readable card.

# Key behaviors
- Fields: display name, emoji, role, tone, values, quirks.
- Backward compatible: migrates an old free-text persona.

# Collaborators
Layered under transient [mood](/components/personality/mood.md); consumed by [session](/components/session.md).
