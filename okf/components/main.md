---
type: Module
title: main.py — wiring & entry point
description: Builds the Telegram application, manager, scheduler, and peer bus, and registers all handlers.
resource: tgbridge/main.py
tags: [module, entrypoint]
timestamp: 2026-06-17T00:00:00Z
---

# Responsibility
Top-level wiring. Sets up logging and the Telegram application, registers
command/callback/media handlers, and starts the long-running services.
`bridge.py` is a thin shim that calls into this.

# Key behaviors
- Builds the python-telegram-bot `Application`.
- Registers [handlers](/components/handlers.md) for commands, callbacks, media.
- Starts the [manager](/components/manager.md), [scheduler](/components/scheduler.md),
  and [peer bus](/components/peers.md).

# Collaborators
[handlers](/components/handlers.md) · [config](/components/config.md) ·
[manager](/components/manager.md) · [peers](/components/peers.md) ·
[scheduler](/components/scheduler.md)
