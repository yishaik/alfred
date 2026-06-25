---
type: Module
title: guards.py — tool guardrails & audit
description: PreToolUse/PostToolUse hooks that block dangerous shell commands, audit every tool call, and echo inline diffs.
resource: tgbridge/guards.py
tags: [module, security, hooks]
timestamp: 2026-06-17T00:00:00Z
---

# Responsibility
Safety layer over tool use. Pattern-matches dangerous commands and requires a tap
before they run — even in auto-approve mode.

# Key behaviors
- Blocks `rm -rf`, force-push, `format`, registry writes, download-piped-to-shell,
  `schtasks /create`, `netsh firewall`, … (extend via `BRIDGE_DANGER_PATTERNS`).
- Appends every tool call to `state/audit.jsonl` (viewable via `/audit`).
- PostToolUse: compact unified diff for each Edit/Write (`BRIDGE_SHOW_DIFFS`).

# Collaborators
[config](/components/config.md) · [fmt](/components/fmt.md) · [metrics](/components/ops/metrics.md)
