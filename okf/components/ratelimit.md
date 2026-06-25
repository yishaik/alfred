---
type: Module
title: ratelimit.py — limiter primitives
description: TokenBucket, PairLimiter, and Backoff primitives guarding against bot-to-bot ping-pong and crash loops.
resource: tgbridge/ratelimit.py
tags: [module, ratelimit, primitives]
timestamp: 2026-06-17T00:00:00Z
---

# Responsibility
Pure rate-limiting primitives used across the bridge. See the tuned defaults in
[rate limits](/operations/rate-limits.md).

# Key behaviors
- `TokenBucket` — generic per-resource throttle.
- `PairLimiter` — per (src,dst) agent-pair buckets for bot-to-bot messages.
- `Backoff` — exponential crash backoff with fresh-session fallback.

# Collaborators
Used by [peers](/components/peers.md), [session](/components/session.md),
[scheduler](/components/scheduler.md).
