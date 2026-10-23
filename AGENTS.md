# Alfred — AGENTS.md (router)

> Alfred = Telegram ↔ Claude Code bridge. Always-on Python process.
> Bot: @AlfredTheTBot · locked to the owner's chat · runs under **systemd** on
> the Oracle box (Ubuntu 24.04 ARM64) as `alfred.service`, with sibling
> bridges `alfred-tlvquest.service` and `alfred-storycut.service`.
> Live code at `/home/ubuntu/alfred/` — do NOT restart mid-session (a restart
> kills the very Claude process doing the work; schedule it:
> `sudo systemd-run --on-active=15 systemctl restart alfred.service`).

## ⚠️ Rules for any agent working here
1. **Never restart the bridge yourself** — the orchestrator does it after you finish (a restart kills your own process)
2. After every file change: `python3 -m py_compile <file>`; tests run with
   `.venv/bin/python selftest.py` (the venv has claude_agent_sdk; system python does not)
3. Never modify .env token VALUES; you MAY append new keys
4. One git commit at the end; NO push
5. Fail-safe is sacred: any error in router/provider → fall through to Claude, never drop a message

## Where to find things

| Need | Go to |
|---|---|
| Workflow for this session | `@AGENT_WORKFLOW.md` |
| Open tasks / backlog | `TODOS.md` |
| Architecture & layout | `README.md` |
| Active feature plan | `PLAN-*.md` (most recent date) |
| Core entry point | `bridge.py` → `supervisor.py` → `tgbridge/main.py` |
| Telegram handlers | `tgbridge/handlers.py` |
| Agent/session management | `tgbridge/manager.py`, `tgbridge/session.py` |
| Model routing | `tgbridge/router.py` |
| Scheduler / reminders | `tgbridge/scheduler.py` |
| Memory / knowledge | `tgbridge/memory.py`, `tgbridge/napkin_store.py` |
| Persistent state | `state/` (agents.json, sessions.json, jobs.json, costs.json) |
| Tests | `selftest.py` — run with `.venv/bin/python selftest.py`; `tests/` via `.venv/bin/python -m pytest -q tests` |
| Compile-check all | `python3 -m py_compile tgbridge/*.py` |

## Module map (one-liners)
- `bridge.py` — entry shim (the .bat/.vbs/.ps1 files in the root are Windows-era leftovers; the live entry is `systemd → supervisor.py → bridge.py`)
- `supervisor.py` — crash-loop backoff + log rotation; wraps main.py
- `tgbridge/main.py` — app init, PTB bot setup, all handlers registered
- `tgbridge/handlers.py` — every Telegram command and message handler
- `tgbridge/manager.py` — named agents: create/stop/list/switch; saves agents.json
- `tgbridge/session.py` — Claude Agent SDK session lifecycle; streaming output
- `tgbridge/router.py` — routes messages to Claude / OpenRouter / free backends
- `tgbridge/scheduler.py` — cron jobs, one-shot reminders, jobs.json persistence
- `tgbridge/memory.py` — long-term memory (pinned notes + knowledge vault)
- `tgbridge/outbox.py` — queue that survives restarts; sends messages in order
- `tgbridge/peers.py` — bot-to-bot messaging (Donna / other agents)
- `tgbridge/voice.py` — voice note transcription via Whisper
- `tgbridge/digest.py` — daily/weekly digest generation
- `tgbridge/proactive.py` — proactive check-in logic
- `tgbridge/tracing.py` — audit.jsonl writer for /audit command
- `tgbridge/guards.py` — permission prompt buttons (Allow/Always/Deny)
- `tgbridge/fmt.py` — markdown → Telegram HTML formatter
- `tgbridge/config.py` — env vars, save_json/load_json, shared constants

## Before you start any task
1. Read `TODOS.md` to understand current priorities
2. Tag `@AGENT_WORKFLOW.md` in your session
3. Run selftest: `.venv/Scripts/python.exe selftest.py`
4. Identify which modules you'll touch (use this file to find them)
