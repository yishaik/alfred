# Alfred — Telegram ↔ Claude Code bridge (v2 — Agent SDK)

Drives always-on `claude` sessions from a Telegram bot locked to your chat.
v2 replaces the raw `claude -p` subprocess with the official **Claude Agent SDK**
(`claude-agent-sdk`), adds multi-agent management, forum-topic threading,
bot-to-bot messaging, a secretary/scheduler, voice notes, live streaming output,
and permission prompts as Telegram buttons.

## Layout
- `bridge.py` — entry shim (keeps `start_bridge.bat`/`.vbs` and the Startup shortcut working)
- `supervisor.py` — restarts the bridge with crash-loop backoff, rotates `bridge.log`
- `tgbridge/` — the implementation (config, session, outbox, manager, scheduler, peers, voice, handlers)
- `state/` — agents.json, sessions.json, jobs.json, costs.json, topics.json, audit.jsonl, bridge-app.log, `tmp/` (all bridge temp files), `backup/` (daily state zips)
- `selftest.py` — offline tests (`python selftest.py`)

## What changed vs v1
| v1 | v2 |
|----|----|
| kill + `--resume` to interrupt | real in-band `client.interrupt()` |
| `--dangerously-skip-permissions` always | per-agent toggle: auto-approve **or** Allow/Always/Deny buttons (read-only tools always auto-allowed) |
| wait for full reply | live streaming — one message edited in place as Claude types |
| plain text output | markdown → Telegram HTML (plain-text fallback), long replies sent as a file |
| queued msgs lost on crash | queue survives restarts and is re-fed |
| crash loop on bad resume | exponential backoff; falls back to a fresh session after 3 fast crashes |
| one session, one cwd | named agents (model/cwd/persona each), forum-topic threads, `/cwd` |

## v2.3 additions
- **/audit** — last tool calls from `state/audit.jsonl` (⛔/✅ marks guarded ones);
  **/logs** — recent warnings/errors from the app log. No more SSH-to-debug.
- **Queue control** — queued messages show their position and a "🗑 Clear queue"
  button; the per-turn footer now includes a token breakdown (in→out, cached).
- **Scheduler fixes** — jobs created in a forum topic now fire back into that
  topic (previously they landed in the private chat); failed one-shot reminders
  retry 3× before being dropped.
- **Supervisor** — `supervisor.py` replaces the bat loop: crash-loop backoff
  (5s→60s→300s on fast exits) and log rotation in one place.
- **Daily state backup** — `state/backup/state-YYYYMMDD.zip` (7 kept) written
  with the health report, so a corrupted agents/jobs/sessions file is recoverable.
- **Error counters** — guard denials, TTS failures, dropped sends, scheduler and
  handler errors counted since start; shown in `/status` and the health report.
- **Security** — peer bus binds `127.0.0.1` unless `BRIDGE_PEER_BIND` says
  otherwise; new danger patterns (download-piped-to-shell, `schtasks /create`,
  `netsh firewall`, `git push --mirror`); audit log archives are timestamped
  instead of overwritten; invalid `BRIDGE_DANGER_PATTERNS` regexes are reported
  at startup instead of silently ignored.

## v2.2 additions
- **Inline diff previews** — every Edit/Write echoes a compact unified diff
  (PostToolUse hook; disable with `BRIDGE_SHOW_DIFFS=0`).
- **Photo albums** — a media group arrives as ONE turn (all paths + caption)
  instead of one turn per photo.
- **/find <text>** — full-text search across past conversations in the agent's
  workdir; tap a hit to resume that session.
- **Telegram command menu** — bot commands registered via `set_my_commands`
  (type "/" to see them).
- **"Always allow" persists** — per agent in `state/agents.json`, survives restarts.
- **Location messages** — shared locations are passed to Claude as lat/lon.

## v2.1 additions
- **Real bridge tools (MCP)** — Claude calls `mcp__bridge__send_file / send_buttons /
  message_agent / schedule / unschedule / list_jobs` (in-process SDK MCP server)
  instead of fragile text markers; the ⟦…⟧ markers still work as a fallback.
- **Undo** — file checkpointing is on; any turn that edited files gets an
  "↩️ Undo file edits" button (SDK `rewind_files`). Last 10 turns kept.
- **Guardrails even in auto-approve** — a `PreToolUse` hook pattern-matches
  dangerous shell commands (`rm -rf`, force-push, `format`, registry writes, …;
  extend via `BRIDGE_DANGER_PATTERNS`) and requires a tap before they run.
  Every tool call is appended to `state/audit.jsonl`.
- **Context gauge** — footer shows context % (warning + "🗜 Compact now" button
  past `BRIDGE_CONTEXT_WARN_PCT`, default 70); also in `/status`.
- **Voice replies** — `/tts on`: replies arrive as voice notes (OpenAI TTS, opus)
  or audio (free `pip install edge-tts`). Voices via `BRIDGE_TTS_VOICE` /
  `BRIDGE_TTS_EDGE_VOICE`.
- **Telegram-native controls** — react 👎/🤮/🤬 to interrupt; edit your last
  message to interrupt + resend the corrected version; replying to any message
  quotes it into Claude's context.
- **Session browser** — `/sessions` lists recent conversations for the agent's
  workdir (tap to resume); `/fork` branches the current conversation.
- **Background task progress** — subagent/background tasks show start/progress
  (throttled)/completion lines.
- **Ops** — daily health report at `BRIDGE_HEALTH_TIME` (default 09:00, "" off),
  monthly budget alerts at 50/80/100% of `BRIDGE_MONTHLY_BUDGET_USD`, app log
  rotation (`state/bridge-app.log`), `bridge.log` rotated by the .bat at ~10MB.
- **Scheduler grammar** — `weekly mon 09:00`, `weekdays 08:30`, and
  `… until 2026-12-31` on any recurrence.

## Modes & features
- **Threaded mode** — set `BRIDGE_GROUP_ID` to a forum supergroup the bot admins.
  Every topic is its own independent Claude session; `/bind <agent>` picks which
  agent config a topic uses. Your private chat keeps working as the "active" agent.
- **Bot management mode** — `/agents` panel: switch ●active, restart, delete;
  `/newagent <name> [workdir]` creates one. Each agent has its own model, cwd,
  persona, secretary flag, approval mode, and sessions.
- **Bot-to-bot** — Claude emits `⟦TO:<agent>|message⟧` to message another local
  agent, or a peer bridge over the token-authenticated HTTP bus
  (`BRIDGE_PEER_PORT/_TOKEN/_PEERS`). Telegram itself doesn't allow bot↔bot DMs.
- **Secretary mode** — `/secretary on` (per agent): persona + reminders/digests.
  Claude can schedule with `⟦REMIND:+30m|tea⟧`, `⟦SCHEDULE:daily 09:00|morning digest⟧`,
  cancel with `⟦UNSCHEDULE:id⟧`. You can too: `/remind 15:00 call mom`, `/jobs`.
- **Dynamic keyboards** — `AskUserQuestion` renders as native buttons (also in
  approvals mode, answered synchronously through the permission layer), and Claude
  can attach quick-reply buttons to any reply with `⟦BUTTONS:Yes|No|Details⟧`.
- **Voice notes** — transcribed via Whisper when `OPENAI_API_KEY` (or
  `GROQ_API_KEY`) is set; transcript echoed back and fed to Claude.
- **Files** — inbound media saved to `<workdir>\inbox\` and announced to Claude;
  outbound via `⟦SEND:<path>⟧` (images as photos).

## Rate limits (loop prevention — on by default)
| guard | default | env |
|-------|---------|-----|
| bot→bot hop depth | 4 | `BRIDGE_MAX_HOPS` |
| msgs per (src,dst) agent pair | 10 / 5 min | `BRIDGE_PAIR_MSGS_PER_5MIN` |
| non-human turns per agent (bots+scheduler) | 30 / hour | `BRIDGE_BOT_TURNS_PER_HOUR` |
| recurring job floor | 15 min | `BRIDGE_MIN_RECUR_MINUTES` |
| max scheduled jobs | 50 | `BRIDGE_MAX_JOBS` |
| crash restarts | exp. backoff, fresh session after 3 fast crashes | — |
| Telegram sends | ≥1.05 s apart per chat, draft edits ≥1.5 s | — |
| long-turn watchdog | warn at 10 min | `BRIDGE_TURN_WARN_SECONDS` |

Dropped messages are announced (`🚦 …dropped: hop limit/rate limit`), never silent.

## Bridge commands
`/start /panel /status /restart /interrupt /kill` ·
`/agents /newagent /delagent /bind` ·
`/auto on|off` (approvals) · `/secretary on|off` · `/tts on|off` · `/cwd <path>` ·
`/jobs` `/remind <when>|<text>` · `/sessions` `/fork` `/find <text>` ·
`/audit` `/logs`
Everything else (including unknown `/slashcommands` like `/clear`, `/compact`,
`/review`) goes straight to Claude. Note: bridge `/status` and `/agents` shadow
Claude's commands of the same name — use the 📋 Commands picker for Claude's.

## Run it
```
pip install -r requirements.txt
python bridge.py            # foreground
start_bridge.vbs            # background, logs to bridge.log, auto-restarts
python selftest.py          # offline sanity check
```

### Python: use a real interpreter, not the Microsoft Store one
The bridge runs on a dedicated **uv venv** (`.venv\`) built from a managed,
standalone CPython — *not* the Microsoft Store Python. The Store Python lives in
an ACL-locked `WindowsApps` folder and **cannot run from a no-login scheduled
task** ("Access is denied"). Recreate the venv with:
```
uv venv --python 3.13 --python-preference only-managed .venv
uv pip install --python .venv\Scripts\python.exe -r requirements.txt
```
`start_bridge.bat` and the supervisor use `.venv\Scripts\python.exe` automatically.

### Autostart at boot, no login required
`start_bridge.vbs` in the Startup folder only runs *after* you log in. To start
the bridge at boot while the machine sits at the lock screen, run **once**:
```
install_autostart.bat        # double-click → approve the UAC prompt
```
This registers a Scheduled Task (`ClaudeTelegramBridge`) that runs ~1 min after
every boot as your account with an **S4U logon** (no stored password), starting
`supervisor.py` on the venv Python. It also disables the now-redundant login
shortcut. Manage it with:
```
schtasks /run    /tn ClaudeTelegramBridge   # start now without rebooting
schtasks /delete /tn ClaudeTelegramBridge /f  # remove autostart
```
Why a task and not a service: Claude Code auth lives in your user profile
(`~/.claude/.credentials.json`), so the bridge must run as **you**, not SYSTEM.
S4U works because that file is plain JSON (no DPAPI). If a future Claude version
encrypts it, switch the task to "Run whether user is logged on or not" with your
password (Task Scheduler → the task → General tab).

## Config (.env next to bridge.py, or environment)
| var | default | |
|-----|---------|-|
| `BRIDGE_BOT_TOKEN` | — | required (or keyring `telegram-claude-bridge/bot_token`) |
| `BRIDGE_CHAT_ID` | — | required, your private chat |
| `BRIDGE_GROUP_ID` | off | forum supergroup for threaded mode |
| `BRIDGE_WORKDIR` | `D:\Projects` | default agent cwd |
| `BRIDGE_MODEL` | default | `opus`/`sonnet`/`haiku`/full id |
| `BRIDGE_CLAUDE_BIN` | auto | path to claude CLI if not on PATH |
| `OPENAI_API_KEY` / `GROQ_API_KEY` | off | voice transcription + OpenAI TTS (OpenAI is tried first; Groq is the transcription fallback) |
| `BRIDGE_TTS_VOICE` / `BRIDGE_TTS_EDGE_VOICE` | `alloy` / `en-US-AriaNeural` | TTS voices (OpenAI / edge-tts) |
| `BRIDGE_PEER_PORT` / `BRIDGE_PEER_TOKEN` / `BRIDGE_PEERS` / `BRIDGE_PEER_NAME` | off | bot-to-bot bus, `PEERS` like `alice=http://host:9001;bob=…` |
| `BRIDGE_PEER_BIND` | `127.0.0.1` | peer-bus listen address; set `0.0.0.0` deliberately for remote peers |
| `BRIDGE_LOCK_PORT` | `49517` | loopback port the single-instance guard binds; a 2nd copy that can't bind it refuses to start |
| `BRIDGE_DANGER_PATTERNS` | — | extra guard regexes, `;`-separated (bad regexes are reported at startup) |
| `BRIDGE_SHOW_DIFFS` | `1` | inline diff previews for file edits |
| `BRIDGE_HEALTH_TIME` | `09:00` | daily health report + state backup ("" disables) |
| `BRIDGE_MONTHLY_BUDGET_USD` | off | budget alerts at 50/80/100% |
| `BRIDGE_CONTEXT_WARN_PCT` | `70` | context gauge warning threshold |
| `BRIDGE_TURN_WARN_SECONDS` | `600` | long-turn watchdog ping |
| rate limits | see table above | `BRIDGE_MAX_HOPS`, `BRIDGE_PAIR_MSGS_PER_5MIN`, `BRIDGE_BOT_TURNS_PER_HOUR`, `BRIDGE_MIN_RECUR_MINUTES`, `BRIDGE_MAX_JOBS` |

The bridge keeps its own temp files (TTS audio, long-reply documents, the
claude subprocess `TEMP`/`TMP`) under `state\tmp\` on the project drive, so a
full `C:` can't break it.

## Runbook
- **Phantom "duplicate" processes (read this first)**: a *single* healthy bridge
  shows up as **four** python processes, not one. The `.venv\Scripts\python.exe`
  is a ~45 KB launcher shim (the venv is built on uv's managed CPython) that
  re-execs the real `…\uv\python\cpython-3.13\python.exe` as a child with the
  same args — and `sys.executable` reports the shim path, so every spawn repeats
  the trick. So one supervisor = `.venv shim → uv real`, one bridge = the same
  pair. Do **not** count `python … bridge.py` instances to detect a duplicate,
  and do **not** "kill the one whose parent isn't supervisor.py" — that is the
  real working bridge. To force a clean restart, run `restart.ps1` (kills
  the whole tree + the `start_bridge.bat` loop, relaunches one instance, logs to
  `restart.log`; add `-Tidy` to also prune orphan session ids).
- **`Conflict: terminated by other getUpdates`**: two *independent launchers* are
  polling the same bot token (e.g. a stray scheduled task plus the Startup
  `.vbs`). The single-instance lock (`BRIDGE_LOCK_PORT`) makes the loser refuse
  to start, so the real symptom is a supervisor **crash-looping** with
  `another instance is already running … Refusing to start` + `exited rc=1` in
  `bridge.log` — that, not the process count, is how you confirm a true
  duplicate. Find the second launcher (Startup folder, `schtasks`, Run keys,
  pm2) and remove it, or run `restart.ps1` to reset to one instance.
- **State file corrupted** (`agents.json`, `jobs.json`, …): stop the bridge,
  restore the file from the newest `state\backup\state-*.zip`, restart.
- **Bot token leaked**: @BotFather → `/revoke` immediately (the token is a
  remote shell on this machine), put the new token in `.env`, restart.
- **C: drive full**: Windows + Claude transcripts degrade even though the
  bridge survives (the startup/health reports warn below 2GB). Free space
  with Disk Cleanup; a reboot may be needed to un-wedge WMI/PowerShell.
- **Bridge won't start / crash loops**: `bridge.log` has the supervisor lines
  (`rc=…`, fast-exit count, backoff); `/logs` and `state\bridge-app.log` have
  the app-level errors. Deleting the agent's entry in `state\sessions.json`
  forces a fresh (non-resumed) session.
- **Deploying changes**: killing `python` is enough for `tgbridge/` edits (the
  supervisor relaunches it). If `start_bridge.bat` or `supervisor.py` changed,
  kill the whole chain (cmd → python supervisor → python bridge → claude) and
  relaunch `start_bridge.vbs` — never edit a `.bat` while cmd is running it.

## Security notes
- The bot token is a remote shell on this machine — treat this folder as secret;
  rotate via @BotFather if leaked. Optional: `pip install keyring` and store it
  in Windows Credential Manager instead of `.env`.
- Auto-approve defaults **on** (v1 behavior). `/auto off` switches the agent to
  tap-to-approve for anything that isn't read-only. Dangerous commands need a
  tap either way (PreToolUse guard), and everything lands in `state/audit.jsonl`
  (`/audit` to view).
- The peer bus refuses to listen without a token, binds loopback by default
  (`BRIDGE_PEER_BIND`), and only accepts authenticated POSTs ≤64KB; still, keep
  it on a LAN/VPN, not the open internet.
