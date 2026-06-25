---
type: Module
title: proactive.py — idle check-ins
description: Autonomous "speak up only if there's a genuine open loop" turns, gated by quiet hours and turn budget.
resource: tgbridge/proactive.py
tags: [module, personality, autonomy]
timestamp: 2026-06-17T00:00:00Z
---

# Responsibility
Lets an agent initiate a turn when idle — but only when it finds a real open
loop, and never as noise.

# Key behaviors
- Respects quiet hours and the non-human turn budget.
- At most one check-in per idle stretch.

# Collaborators
Pure logic; driven by the [manager](/components/manager.md). Sibling of the
overnight [dream](/components/personality/dream.md) brief.
