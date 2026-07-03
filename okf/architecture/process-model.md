---
type: Runbook
title: Process & runtime model
description: How the bridge runs — supervisor, the uv-venv launcher shim, the "4 python processes is healthy" rule, single-instance lock, and S4U autostart.
tags: [runbook, process, autostart, ops]
timestamp: 2026-06-17T00:00:00Z
---

# Process tree

```
start_bridge.bat → python supervisor.py → python bridge.py → claude (Agent SDK)
```

[supervisor](/components/ops/supervisor.md) restarts [bridge.py](/components/main.md)
with exponential backoff (5s→60s→300s on fast exits) and rotates `bridge.log`.

# The "phantom duplicate" rule

A **single healthy bridge shows up as four python processes**, not one. The
`.venv\Scripts\python.exe` is a ~45 KB uv launcher shim that re-execs the real
managed CPython as a child with the same args (and `sys.executable` reports the
shim path, so every spawn repeats the trick). So:

- Do **not** count `python … bridge.py` instances to detect a duplicate.
- Do **not** kill "the one whose parent isn't supervisor.py" — that is the real bridge.
- To force a clean restart, run `restart.ps1` (kills the whole tree + the
  `start_bridge.bat` loop, relaunches one instance, logs to `restart.log`;
  add `-Tidy` to also prune orphan session ids).

# Single-instance lock

The bridge binds a loopback port (`BRIDGE_LOCK_PORT`, default 49517). A second
launcher that can't bind it refuses to start. The real symptom of a true
duplicate is a supervisor **crash-looping** with "another instance is already
running … Refusing to start" + `exited rc=1` — not the process count. The
Telegram tell is `Conflict: terminated by other getUpdates`.

# Autostart (boot, no login)

`install_autostart.bat` registers a Scheduled Task (`ClaudeTelegramBridge`) that
runs ~1 min after boot as your account with an **S4U logon** (no stored
password), starting [supervisor.py](/components/ops/supervisor.md) on the venv
Python. It runs as **you** (not SYSTEM) because Claude auth lives in
`~/.claude/.credentials.json` in your profile. S4U works because that file is
plain JSON; if a future Claude encrypts it, switch the task to "Run whether user
is logged on or not" with a password.

See the full [runbook](/operations/runbook.md) for failure recovery.

# Citations
[1] [Project README — Runbook](/references/readme.md)
