---
type: Reference
title: Config reference (.env / environment)
description: The BRIDGE_* and related environment variables, their defaults, and what they control.
tags: [reference, config]
timestamp: 2026-06-17T00:00:00Z
---

Resolved by [config.py](/components/config.md). Place in `.env` next to `bridge.py`,
or the environment.

# Required
| var | notes |
|-----|-------|
| `BRIDGE_BOT_TOKEN` | required (or keyring `telegram-claude-bridge/bot_token`) |
| `BRIDGE_CHAT_ID` | your private chat |

# Routing & model
| var | default | |
|-----|---------|-|
| `BRIDGE_GROUP_ID` | off | forum supergroup for threaded mode |
| `BRIDGE_WORKDIR` | `D:\Projects` | default agent cwd |
| `BRIDGE_MODEL` | default | `opus`/`sonnet`/`haiku`/full id |
| `BRIDGE_CLAUDE_BIN` | auto | path to claude CLI if not on PATH |

# Voice
| var | default | |
|-----|---------|-|
| `OPENAI_API_KEY` / `GROQ_API_KEY` | off | transcription + OpenAI TTS (OpenAI first, Groq fallback) |
| `BRIDGE_TTS_VOICE` / `BRIDGE_TTS_EDGE_VOICE` | `alloy` / `en-US-AriaNeural` | TTS voices |

# Peer bus ([peers](/components/peers.md))
| var | default | |
|-----|---------|-|
| `BRIDGE_PEER_PORT` / `_TOKEN` / `_PEERS` / `_NAME` | off | bot-to-bot bus; `PEERS` like `alice=http://host:9001;bob=…` |
| `BRIDGE_PEER_BIND` | `127.0.0.1` | listen address; set `0.0.0.0` deliberately |
| `BRIDGE_LOCK_PORT` | `49517` | single-instance guard port |

# Safety & ops
| var | default | |
|-----|---------|-|
| `BRIDGE_DANGER_PATTERNS` | — | extra [guard](/components/guards.md) regexes, `;`-separated |
| `BRIDGE_SHOW_DIFFS` | `1` | inline diff previews |
| `BRIDGE_HEALTH_TIME` | `09:00` | daily health report + state backup ("" disables) |
| `BRIDGE_MONTHLY_BUDGET_USD` | off | budget alerts at 50/80/100% |
| `BRIDGE_CONTEXT_WARN_PCT` | `70` | context gauge warning |
| `BRIDGE_TURN_WARN_SECONDS` | `600` | long-turn watchdog |
| rate limits | see [rate limits](/operations/rate-limits.md) | `BRIDGE_MAX_HOPS`, … |

# Citations
[1] [Project README — Config](/references/readme.md)
