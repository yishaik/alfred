---
type: Module
title: dream.py — overnight brief
description: Builds a morning brief (yesterday's recap + today's agenda) by reusing the digest and scheduled jobs.
resource: tgbridge/dream.py
tags: [module, personality, secretary]
timestamp: 2026-06-17T00:00:00Z
---

# Responsibility
The overnight pass: a morning brief combining a recap and an agenda. Pure
builders (no I/O).

# Key behaviors
- Reuses [digest](/components/ops/digest.md) for yesterday's summary.
- Builds the agenda from the [scheduler](/components/scheduler.md)'s jobs.

# Collaborators
[digest](/components/ops/digest.md) · [scheduler](/components/scheduler.md)
