---
type: Module
title: markers.py — directive parser
description: Parses legacy ⟦…⟧ directives (SEND, TO, REMIND, SCHEDULE, BUTTONS, UNSCHEDULE) from Claude's replies.
resource: tgbridge/markers.py
tags: [module, parsing]
timestamp: 2026-06-17T00:00:00Z
---

# Responsibility
Pure parser for the structured `⟦…⟧` text markers Claude can emit — the fallback
path now that [bridgetools](/components/bridgetools.md) exposes real MCP tools.

# Key behaviors
- Parses and strips: `⟦SEND:path⟧`, `⟦TO:agent|msg⟧`, `⟦REMIND:when|text⟧`,
  `⟦SCHEDULE:when|prompt⟧`, `⟦UNSCHEDULE:id⟧`, `⟦BUTTONS:a|b⟧`.
- Flexible when-formats for scheduling (consumed by the [scheduler](/components/scheduler.md)).

# Collaborators
Pure parsing. Used by [session](/components/session.md) and [scheduler](/components/scheduler.md).
