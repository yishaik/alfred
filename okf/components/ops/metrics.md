---
type: Module
title: metrics.py — event counters
description: In-process counters (reset on restart) for /status and the daily health report.
resource: tgbridge/metrics.py
tags: [module, ops]
timestamp: 2026-06-17T00:00:00Z
---

# Responsibility
A simple counter map answering "what went wrong since last restart." Long-term
history lives in logs and the audit trail.

# Key behaviors
- Counts guard denials, TTS failures, dropped sends, scheduler/handler errors.
- Surfaced in `/status` and the health report.

# Collaborators
Written by [guards](/components/guards.md), [scheduler](/components/scheduler.md),
[handlers](/components/handlers.md); read by [digest](/components/ops/digest.md).
