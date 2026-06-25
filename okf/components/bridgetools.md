---
type: Module
title: bridgetools.py — in-process MCP server
description: Exposes bridge actions to Claude as real MCP tools (send_file, send_buttons, message_agent, schedule, remember/forget/recall).
resource: tgbridge/bridgetools.py
tags: [module, mcp, tools]
timestamp: 2026-06-17T00:00:00Z
---

# Responsibility
An in-process SDK MCP server, one instance per [session](/components/session.md),
that turns fragile `⟦…⟧` [markers](/components/markers.md) into proper tool calls.

# Key behaviors
- Tools: `mcp__bridge__send_file`, `send_buttons`, `message_agent`,
  `schedule`/`unschedule`/`list_jobs`, `remember`/`forget`/`recall`.
- Runs in-process (no subprocess); the legacy markers remain a fallback.

# Collaborators
[session](/components/session.md) (host) · feeds the [scheduler](/components/scheduler.md)
and [peers](/components/peers.md).
