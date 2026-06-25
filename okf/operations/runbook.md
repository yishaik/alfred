---
type: Runbook
title: Operations runbook
description: Failure recovery — phantom duplicates, getUpdates conflicts, corrupted state, leaked token, full disk, deploying changes.
tags: [runbook, ops]
timestamp: 2026-06-17T00:00:00Z
---

# Phantom "duplicate" processes (read first)
A single healthy bridge is **four** python processes — see
[the process model](/architecture/process-model.md). Don't count `bridge.py`
instances; don't kill "the one whose parent isn't supervisor.py." Force a clean
restart with `restart_bridge.ps1`.

# `Conflict: terminated by other getUpdates`
Two independent launchers poll the same token. The
[single-instance lock](/architecture/process-model.md) makes the loser refuse to
start, so the real symptom is a [supervisor](/components/ops/supervisor.md)
crash-looping with "another instance is already running." Find the second
launcher (Startup folder, `schtasks`, Run keys, pm2) and remove it.

# State file corrupted (agents.json, jobs.json, …)
Stop the bridge, restore the file from the newest `state/backup/state-*.zip`, restart.

# Bot token leaked
@BotFather → `/revoke` immediately (the token is a remote shell on this machine),
put the new token in `.env`, restart.

# C: drive full
Windows + Claude transcripts degrade even though the bridge survives (startup/health
reports warn below 2GB). Free space; a reboot may be needed to un-wedge WMI/PowerShell.

# Bridge won't start / crash loops
`bridge.log` has supervisor lines (`rc=…`, fast-exit count, backoff); `/logs` and
`state/bridge-app.log` have app errors. Deleting the agent's entry in
`state/sessions.json` forces a fresh (non-resumed) session.

# Deploying changes
Killing `python` is enough for [tgbridge](/components/main.md) edits (the
supervisor relaunches). If `start_bridge.bat` or
[supervisor.py](/components/ops/supervisor.md) changed, kill the whole chain and
relaunch `start_bridge.vbs` — never edit a `.bat` while cmd is running it.

# Citations
[1] [Project README — Runbook](/references/readme.md)
