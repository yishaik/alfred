---
type: Module
title: manager.py — agents, sessions & routing
description: Registry of agents and sessions; routes every message (private/topic/bot-to-bot) and runs daily health/digest reports.
resource: tgbridge/manager.py
tags: [module, routing, core]
timestamp: 2026-06-17T00:00:00Z
---

# Responsibility
The hub. Holds agents, [sessions](/components/session.md), costs, memory, todos,
and watchers, and routes inbound messages to the right session.

# Key behaviors
- Routes: private chat, forum topics, bot-to-bot.
- Drives the [scheduler](/components/scheduler.md), [escalation](/components/ops/escalate.md),
  [proactive](/components/personality/proactive.md) checks, and memory decay.
- Emits the daily health/[digest](/components/ops/digest.md) report.

# Collaborators
[session](/components/session.md) · [memory](/components/memory.md) ·
[todos](/components/mini-apps/todos.md) · [expenses](/components/mini-apps/expenses.md) ·
[contacts](/components/mini-apps/contacts.md) · [scheduler](/components/scheduler.md) ·
[peers](/components/peers.md) · [watchers](/components/ops/watchers.md) ·
[digest](/components/ops/digest.md) · [escalate](/components/ops/escalate.md) ·
[proactive](/components/personality/proactive.md)
