# Bridge hardening + UX plan — 2026-07-03

Executor: an Opus agent working INSIDE the live bridge repo (D:\Projects\telegram-claude-bridge).
⚠️ RULES: (1) Do NOT restart the bridge yourself — the orchestrator restarts once at the end
(a restart kills your own process). (2) Don't touch .env / tokens / state JSON contents beyond
what a task says. (3) After each file change: `python -m py_compile <file>`. (4) One git commit
at the end (do not push). (5) Audit findings referenced below were verified on 2026-07-03;
re-locate line numbers before editing — they may have drifted.

## Phase A — stability (from the code audit)

**A1. Serialize state writes (the one real HIGH).**
All `save_json` writers (`manager.py` save_agents/save_topics/save_session_id, scheduler `_save`,
`add_cost`, watchers/todos if present) can race from concurrent handlers → corrupt JSON on Windows.
Add ONE `threading.Lock` (sync code) or `asyncio.Lock` (async paths) around the write funnel —
simplest: put the lock inside `config.save_json` itself (it's the single funnel; a module-level
`threading.Lock` covers both async handlers and the scheduler loop since the file I/O is sync).

**A2. Graceful shutdown.**
- `main.py` post_shutdown: wrap `m.stop_all()` in `asyncio.wait_for(..., timeout=30)`;
  call `m.scheduler._save()` before stopping; drain/flush outboxes before sessions disconnect.
- `supervisor.py`: add SIGTERM handler → clean exit (`signal.signal(SIGTERM, ...)`).

**A3. Stop swallowing errors.**
- `peers.py` (~129-133): bare except → `log.warning("peer message failed: %s", e)` and return 500.
- `handlers.py` (~1527, 1539) `except Exception: pass` → `log.debug(...)` at minimum.
- `session.py` `_react()` task: log failures at debug.
- `outbox.py` (~310): include the last exception in the "dropped message" error line.

**A4. Prune orphan sessions on startup.**
In `AgentManager.__init__` after loading `sessions.json`: keep only keys that are
`<agent>@p` for an existing agent, `<agent>@t<tid>` where `topics.json[tid]==agent`, or `main@t0`.
Log dropped keys once. (Replaces the offline node prune in tidy_restart.ps1.)

**A5. Scheduled-job routing hardening.**
- In scheduler `_fire` (or `session_for_job`): if the job's route is `@t<tid>` and `tid` is no
  longer in `topics.json` → log a warning and fall back to `<agent>@p` delivery (never silent-fail).
- Prefix every scheduled-job delivery with a small header line the user sees:
  `⏰ job #<id> · <agent>` — so it's obvious what fired and for which agent (we saw a `flights`
  job render in `main` with no indication).

**A6. One restart script.**
Merge `restart_bridge.ps1` + `tidy_restart.ps1` into a single `restart.ps1` with `-Tidy` switch
(tidy = also prune legacy state, in PowerShell only — no node dependency). Delete
`collapse_dupes.ps1` and `tidy_restart.ps1` (the "duplicate tree" they chased was a misdiagnosis —
the uv wrapper chain is normal; health check = exactly one owner of lock port 49517).
Keep step-by-step logging to restart.log.

## Phase B — UX

**B1. "Back online" notice.**
`/restart` command and `restart.ps1` write a flag file `state/.restart-pending` before killing.
On startup (post_init), if the flag exists: delete it and send "♻️ חזרתי — הגשר באוויר" to the
main chat. No message on ordinary crash-respawns (flag absent) to avoid noise.

**B2. Telegram command menu (discoverability).**
On startup call `bot.set_my_commands` with the core commands + SHORT Hebrew descriptions:
panel (לוח בקרה), status (מצב), jobs (תזמונים), remind (תזכורת), bind (חיבור topic לסוכן),
model (החלפת מודל), restart (אתחול session), interrupt (עצירה), mute/costs as fits.
Keep under Telegram's 100-command cap; descriptions ≤ 256 chars.

**B3. /jobs polish.**
Numbered list, next-run shown in Asia/Jerusalem local time, and per-job inline ❌-cancel button
(callback `job:cancel:<id>`), plus a ↻ refresh button. Reuse existing jobs_kb if present.

**B4. Friendly turn-failure surfacing.**
When a turn dies (SDK error/crash path in session.py): emit ONE short Hebrew line to the chat —
"⚠️ התקלה: <gist> — נסה שוב או /restart" — instead of silence. Make sure the crash-restart path
already existing doesn't double-message.

**B5. Job-delivery header** — covered by A5 (the `⏰ job #id · agent` prefix).

**B6. Bilingual /start + /panel header.**
The /start help text is English-only; the owner is Hebrew-speaking. Make the header + section
titles Hebrew with the English command names kept as-is (commands stay /english).

## Verification (must all pass before commit)
1. `python -m py_compile` on every touched file.
2. `python selftest.py` (exists in repo root) — must pass.
3. Simulated race: a tiny script that calls save_json from 20 threads/tasks — file stays valid JSON.
4. grep: no remaining `except Exception:\s*pass` in tgbridge/ (except explicitly justified with a comment).
5. `restart.ps1` parses (`powershell -NoProfile -Command "Get-Command -Syntax"` on it or `-WhatIf` dry path) — do NOT execute it.
6. Git: single commit "hardening + UX: state-write lock, graceful shutdown, error surfacing, job routing/headers, restart notice, HE command menu, /jobs buttons" with Co-Authored-By trailer. NO push.

## Out of scope (explicitly)
- Splitting handlers.py / session.py into submodules (worthwhile, separate PR).
- Any change to model picker (done earlier today), peer protocol, or voice.
- Restarting the bridge (orchestrator does it after you finish).
