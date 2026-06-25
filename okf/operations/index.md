# Concepts

* [Bridge commands](commands.md) - The slash commands the bridge itself handles; everything else passes through to Claude.
* [Config reference (.env / environment)](config-reference.md) - The BRIDGE_* and related environment variables, their defaults, and what they control.
* [Rate limits (loop prevention)](rate-limits.md) - The on-by-default guards against bot-to-bot loops and runaway turns, with their env overrides.
* [Operations runbook](runbook.md) - Failure recovery — phantom duplicates, getUpdates conflicts, corrupted state, leaked token, full disk, deploying changes.
