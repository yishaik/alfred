---
type: Module
title: peers.py — bot-to-bot transport
description: Token-authenticated HTTP bus for bridge-to-bridge messages, with hop counters and per-pair rate limits.
resource: tgbridge/peers.py
tags: [module, networking, bot-to-bot]
timestamp: 2026-06-17T00:00:00Z
---

# Responsibility
A tiny HTTP bus letting one bridge message another (Telegram itself forbids
bot↔bot DMs). Configured via `BRIDGE_PEER_PORT/_TOKEN/_PEERS/_NAME`.

# Key behaviors
- Binds `127.0.0.1` unless `BRIDGE_PEER_BIND` says otherwise; refuses to listen without a token.
- Accepts only authenticated POSTs ≤64KB.
- Hop counters prevent infinite loops; per-pair [rate limits](/components/ratelimit.md) throttle ping-pong.

# Collaborators
[config](/components/config.md) · [ratelimit](/components/ratelimit.md)
