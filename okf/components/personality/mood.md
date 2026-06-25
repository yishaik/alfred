---
type: Module
title: mood.py — transient mood
description: A lightweight transient mood (recovering/cautious/weary/in-the-zone/neutral) layered on the stable soul.
resource: tgbridge/mood.py
tags: [module, personality]
timestamp: 2026-06-17T00:00:00Z
---

# Responsibility
Cheap, transient affect on top of the [soul](/components/personality/soul.md),
injected per-turn only when it changes.

# Key behaviors
- States: recovering, cautious, weary, in-the-zone, neutral.
- Driven by cheap signals: turn count, error/win streaks, crash recovery.

# Collaborators
[soul](/components/personality/soul.md) · [session](/components/session.md)
