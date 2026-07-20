---
type: Module
title: memory_dreaming.py — background memory synthesis
description: Tool-less nightly consolidation of recent conversations into safe, current long-term memory.
resource: tgbridge/memory_dreaming.py
tags: [module, memory, dreaming]
timestamp: 2026-07-20T00:00:00Z
---

# Responsibility
Turn recent user conversations into a small, validated memory mutation plan.
The pass runs from Dream mode without delaying the morning brief.

# Flow
1. Collect unseen `(user, assistant)` pairs from each agent's rolling history.
2. Compare them with the current per-agent Napkin memory snapshot.
3. Ask a short-lived Claude process with `allowed_tools=[]` for JSON only.
4. Reject secrets, auto-pinning, ambiguous deletes, and malformed operations.
5. Apply safe note/fact additions and unique non-pinned removals.
6. Save fingerprints and a local audit event under `state/`.

# Controls
- `MEMORY_DREAM_MODEL` selects the synthesis model; default: `haiku`.
- `MEMORY_DREAM_MINUTES` is available for lifecycle-managed periodic use;
  Dream mode currently schedules one pass nightly.
- Pinned memories are never automatically created, changed, or deleted.
