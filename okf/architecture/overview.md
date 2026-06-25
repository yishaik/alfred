---
type: Architecture
title: Alfred — architecture overview
description: Always-on Telegram bot that drives Claude Agent SDK sessions, one per chat/forum-topic, with streaming, permissions, and a secretary.
tags: [architecture, telegram, claude, agent-sdk]
timestamp: 2026-06-17T00:00:00Z
---

# What Alfred is

Alfred drives always-on `claude` sessions from a Telegram bot locked to one chat.
v2 replaces the raw `claude -p` subprocess with the official **Claude Agent SDK**
(`claude-agent-sdk`), adding multi-agent management, forum-topic threading,
bot-to-bot messaging, a secretary/scheduler, voice notes, live streaming output,
and permission prompts rendered as Telegram buttons.

# Layers

- **Process supervision** — [supervisor](/components/ops/supervisor.md) runs
  [bridge.py](/components/main.md) forever with crash-loop backoff and log rotation.
  See [the process model](/architecture/process-model.md).
- **Wiring** — [main](/components/main.md) builds the Telegram app, the
  [manager](/components/manager.md), the [scheduler](/components/scheduler.md),
  and the [peer bus](/components/peers.md).
- **Routing & state** — [manager](/components/manager.md) is the registry of
  agents and [sessions](/components/session.md) and routes every inbound message
  (private chat, forum topic, or bot-to-bot).
- **Per-route session** — one [session](/components/session.md) wraps a
  long-lived Agent SDK client, queues turns, recovers from crashes, and applies
  [guards](/components/guards.md), [personality](/components/personality/soul.md),
  and [rate limits](/components/ratelimit.md).
- **I/O** — [handlers](/components/handlers.md) take Telegram in;
  [outbox](/components/outbox.md) + [fmt](/components/fmt.md) push replies out;
  [voice](/components/voice.md) does STT/TTS; [bridgetools](/components/bridgetools.md)
  exposes bridge actions to Claude as real MCP tools.

# Key design choices

- **One session per route** — private chat and each forum topic are independent
  Claude sessions; `/bind <agent>` picks the config a topic uses.
- **Tools over text markers** — Claude calls `mcp__bridge__*` tools; the legacy
  `⟦…⟧` [markers](/components/markers.md) remain a fallback.
- **Permissions as buttons** — auto-approve by default; `/auto off` makes
  non-read-only tools tap-to-approve; dangerous commands always need a tap.
- **Survives restarts** — queued messages, sessions, jobs, and memory persist
  under `state/`; a corrupted state file restores from `state/backup/`.

# Citations
[1] [Project README](/references/readme.md)
[2] [Claude Agent SDK](/references/claude-agent-sdk.md)
