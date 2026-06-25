---
type: Reference
title: Telegram Bot API
description: The Telegram Bot API and python-telegram-bot library underlying the bridge's I/O.
resource: https://core.telegram.org/bots/api
tags: [reference, external, telegram]
timestamp: 2026-06-17T00:00:00Z
---

The bridge's transport. [handlers](/components/handlers.md) consume updates and
[outbox](/components/outbox.md) sends replies, both via python-telegram-bot.
Relevant primitives: forum topics (threaded mode), inline keyboards (approvals,
quick replies), `set_my_commands` (the command menu), message editing (live
streaming drafts), media groups (photo albums), and the per-chat send rate limits.

# Citations
[1] https://core.telegram.org/bots/api
