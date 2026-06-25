---
type: Module
title: digest.py — daily digest & health
description: Summarizes the audit trail per agent (tool counts, costs, denials) for the daily health report.
resource: tgbridge/digest.py
tags: [module, ops, reporting]
timestamp: 2026-06-17T00:00:00Z
---

# Responsibility
Builds the daily digest / health report from `state/audit.jsonl`. Pure parsing,
no LLM calls.

# Key behaviors
- Per-agent tool categorization and breakdown.
- Costs, denials, error counters.

# Collaborators
[config](/components/config.md); reused by the overnight [dream](/components/personality/dream.md) brief.
