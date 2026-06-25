---
type: Reference
title: Bridge commands
description: The slash commands the bridge itself handles; everything else passes through to Claude.
tags: [reference, commands]
timestamp: 2026-06-17T00:00:00Z
---

Handled by [handlers.py](/components/handlers.md). Everything else (including
unknown `/slashcommands` like `/clear`, `/compact`, `/review`) goes straight to Claude.

# Lifecycle
`/start` `/panel` `/status` `/restart` `/interrupt` `/kill`

# Agents
`/agents` `/newagent` `/delagent` `/bind`

# Modes (per agent)
`/auto on|off` (approvals) · `/secretary on|off` · `/tts on|off` · `/cwd <path>`

# Secretary & scheduler ([scheduler](/components/scheduler.md))
`/jobs` · `/remind <when>|<text>`

# Conversation
`/sessions` `/fork` `/find <text>` ([transcripts](/components/ops/transcripts.md))

# Diagnostics
`/audit` (from `state/audit.jsonl`) · `/logs` (recent warnings/errors)

> Note: bridge `/status` and `/agents` shadow Claude's same-named commands — use
> the 📋 Commands picker for Claude's.

# Citations
[1] [Project README — Bridge commands](/references/readme.md)
