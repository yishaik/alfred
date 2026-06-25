---
type: Module
title: scheduler.py — jobs & reminders
description: Persistent job scheduler for /remind, ⟦SCHEDULE⟧, and recurring tasks, with caps and a recurrence floor.
resource: tgbridge/scheduler.py
tags: [module, scheduler, secretary]
timestamp: 2026-06-17T00:00:00Z
---

# Responsibility
Fires reminders and prompts at scheduled times. Jobs persist in `state/jobs.json`
and survive restarts.

# Key behaviors
- Grammar: `15:00`, `+30m`, `daily 09:00`, `weekly mon 09:00`, `weekdays 08:30`,
  ` until 2026-12-31`.
- Caps (`BRIDGE_MAX_JOBS`, default 50) and recurrence floor (`BRIDGE_MIN_RECUR_MINUTES`, 15 min).
- Jobs created in a forum topic fire back into that topic; failed one-shots retry 3×.
- Budgets non-human turns to prevent runaway.

# Collaborators
[config](/components/config.md) · [markers](/components/markers.md) ·
[session](/components/session.md) · [metrics](/components/ops/metrics.md)
