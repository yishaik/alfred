---
type: Module
title: config.py — settings, secrets, state dirs
description: Environment-based config, secrets, and the persistent STATE_DIR / TMP_DIR, plus disk-space checks.
resource: tgbridge/config.py
tags: [module, config, foundational]
timestamp: 2026-06-17T00:00:00Z
---

# Responsibility
Foundational settings layer. Loads `BRIDGE_BOT_TOKEN`, `BRIDGE_CHAT_ID`, workdir
and the many `BRIDGE_*` knobs from env/`.env`; owns `STATE_DIR` and `TMP_DIR`.

# Key behaviors
- Resolves required secrets (token/chat) and optional tuning vars.
- Manages `state/` and `state/tmp/` on the project drive (a full `C:` can't break it).
- Disk-space checks and temp cleanup.

# Collaborators
Foundational — imported by nearly everything. See the full
[config reference](/operations/config-reference.md).
