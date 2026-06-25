---
type: Module
title: escalate.py — auto-escalation alerts
description: Edge-triggered alerts for low disk, queue backlog, and crash runs (once per condition).
resource: tgbridge/escalate.py
tags: [module, ops, alerting]
timestamp: 2026-06-17T00:00:00Z
---

# Responsibility
Surfaces operational problems proactively, without spamming.

# Key behaviors
- Edge-triggered: fires once per condition.
- Monitors system/project disk, session queue depth, crash rate.

# Collaborators
Pure assessment; driven by the [manager](/components/manager.md).
