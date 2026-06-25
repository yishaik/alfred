---
type: Reference
title: Rate limits (loop prevention)
description: The on-by-default guards against bot-to-bot loops and runaway turns, with their env overrides.
tags: [reference, ratelimit, safety]
timestamp: 2026-06-17T00:00:00Z
---

Enforced via [ratelimit.py](/components/ratelimit.md) primitives. Dropped messages
are announced (`🚦 …dropped: hop limit/rate limit`), never silent.

| guard | default | env |
|-------|---------|-----|
| bot→bot hop depth | 4 | `BRIDGE_MAX_HOPS` |
| msgs per (src,dst) agent pair | 10 / 5 min | `BRIDGE_PAIR_MSGS_PER_5MIN` |
| non-human turns per agent | 30 / hour | `BRIDGE_BOT_TURNS_PER_HOUR` |
| recurring job floor | 15 min | `BRIDGE_MIN_RECUR_MINUTES` |
| max scheduled jobs | 50 | `BRIDGE_MAX_JOBS` |
| crash restarts | exp. backoff, fresh session after 3 fast crashes | — |
| Telegram sends | ≥1.05 s apart per chat, draft edits ≥1.5 s | — |
| long-turn watchdog | warn at 10 min | `BRIDGE_TURN_WARN_SECONDS` |

# Citations
[1] [Project README — Rate limits](/references/readme.md)
