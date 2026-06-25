---
type: Module
title: supervisor.py — crash-loop supervisor
description: Runs bridge.py forever with exponential backoff and rotates bridge.log; no third-party deps.
resource: supervisor.py
tags: [module, ops, process]
timestamp: 2026-06-17T00:00:00Z
---

# Responsibility
Keeps the bridge alive. Restarts [bridge.py](/components/main.md) with escalating
delays and rotates logs. See [the process model](/architecture/process-model.md).

# Key behaviors
- Backoff on fast exits: 5s → 60s → 300s.
- Rotates `bridge.log` at ~10MB.
- No third-party deps — runs even with a broken venv.

# Collaborators
[bridge.py / main](/components/main.md)
