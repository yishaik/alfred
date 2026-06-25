---
type: Reference
title: Claude Agent SDK
description: The official SDK (claude-agent-sdk) that v2 uses to drive long-lived Claude sessions.
resource: https://docs.claude.com/en/api/agent-sdk
tags: [reference, external, claude]
timestamp: 2026-06-17T00:00:00Z
---

v2 replaced the raw `claude -p` subprocess with the official **Claude Agent SDK**
(`claude-agent-sdk`). It provides the long-lived client wrapped by
[session.py](/components/session.md): in-band `interrupt()`, streaming output,
PreToolUse/PostToolUse hooks (used by [guards](/components/guards.md)), in-process
MCP servers (used by [bridgetools](/components/bridgetools.md)), file checkpoint/
rewind (Undo), and per-tool permission callbacks rendered as Telegram buttons.

# Citations
[1] https://docs.claude.com/en/api/agent-sdk
