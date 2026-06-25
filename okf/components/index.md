# Subdirectories

* [mini-apps](mini-apps/index.md)
* [ops](ops/index.md)
* [personality](personality/index.md)

# Concepts

* [bridgetools.py — in-process MCP server](bridgetools.md) - Exposes bridge actions to Claude as real MCP tools (send_file, send_buttons, message_agent, schedule, remember/forget/recall).
* [config.py — settings, secrets, state dirs](config.md) - Environment-based config, secrets, and the persistent STATE_DIR / TMP_DIR, plus disk-space checks.
* [fmt.py — markdown → Telegram HTML](fmt.md) - Converts markdown to Telegram HTML, splits long messages under the 4000-char limit, and summarizes tool calls.
* [guards.py — tool guardrails & audit](guards.md) - PreToolUse/PostToolUse hooks that block dangerous shell commands, audit every tool call, and echo inline diffs.
* [handlers.py — Telegram input](handlers.md) - Telegram handlers for commands, button callbacks, media uploads, and voice transcription; routes messages to sessions.
* [main.py — wiring & entry point](main.md) - Builds the Telegram application, manager, scheduler, and peer bus, and registers all handlers.
* [manager.py — agents, sessions & routing](manager.md) - Registry of agents and sessions; routes every message (private/topic/bot-to-bot) and runs daily health/digest reports.
* [markers.py — directive parser](markers.md) - Parses legacy ⟦…⟧ directives (SEND, TO, REMIND, SCHEDULE, BUTTONS, UNSCHEDULE) from Claude's replies.
* [memory.py — long-term agent memory](memory.md) - Pinned/note/fact items that survive restarts, are injected into every session, and decay over time.
* [outbox.py — delivery queue](outbox.md) - Per-route delivery queue with batching, throttling, streaming-draft edits, and HTML→plain-text fallback.
* [peers.py — bot-to-bot transport](peers.md) - Token-authenticated HTTP bus for bridge-to-bridge messages, with hop counters and per-pair rate limits.
* [ratelimit.py — limiter primitives](ratelimit.md) - TokenBucket, PairLimiter, and Backoff primitives guarding against bot-to-bot ping-pong and crash loops.
* [scheduler.py — jobs & reminders](scheduler.md) - Persistent job scheduler for /remind, ⟦SCHEDULE⟧, and recurring tasks, with caps and a recurrence floor.
* [session.py — the Claude Agent SDK client](session.md) - One long-lived Agent SDK client per chat/topic; queues turns, recovers from crashes, applies guards, personality, and rate limits.
* [voice.py — speech in & out](voice.md) - Voice transcription (OpenAI Whisper / Groq) and TTS replies (OpenAI voice notes or free edge-tts).
