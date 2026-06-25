---
type: Module
title: transcripts.py — conversation search
description: Full-text search over local Claude Code session transcripts, scoped to an agent's workdir.
resource: tgbridge/transcripts.py
tags: [module, ops, search]
timestamp: 2026-06-17T00:00:00Z
---

# Responsibility
Powers `/find <text>` — searching past conversations so you can resume one.

# Key behaviors
- Searches `~/.claude/projects` by workdir.
- Newest-first; one hit per session, max 8 results.

# Collaborators
Uses the Claude SDK; invoked via [handlers](/components/handlers.md).
