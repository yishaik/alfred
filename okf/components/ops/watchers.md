---
type: Module
title: watchers.py — passive change watchers
description: Polls files/dirs/git repos with cheap fingerprints and feeds the agent proactive turns on change.
resource: tgbridge/watchers.py
tags: [module, ops]
timestamp: 2026-06-17T00:00:00Z
---

# Responsibility
Passive monitoring. Detects changes cheaply and lets the agent react.

# Key behaviors
- Cheap fingerprints: mtime/size, dir hash, git HEAD.
- Never reads file contents.

# Collaborators
Driven by [manager](/components/manager.md); pairs with [proactive](/components/personality/proactive.md) turns.
