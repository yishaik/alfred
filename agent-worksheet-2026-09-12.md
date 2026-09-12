# Direct LG webOS remote

Workflow: @AGENT_WORKFLOW.md

## Plan
- Add an in-process LG webOS client and an authorized Telegram `/tv` remote.
- Preserve existing local edits; route the legacy remote labels through the direct client.
- Persist pairing under ignored `state/`, document setup, and verify offline plus live read-only behavior.

## Evidence before changes
- Existing remote labels call an unavailable HTTP daemon on port 8891 and report success even on failure.
- Saved TV endpoint is reachable. aiowebostv 0.9.2 connected using the existing local key; read volume 12 and foreground YouTube successfully.
- Baseline selftest has one existing failure: the live bridge already holds the singleton port. All other checks pass. Final selftest will use an isolated test port.
- Pre-existing edits: restart.ps1, selftest.py, tgbridge/bridgetools.py, tgbridge/handlers.py, tgbridge/router.py and untracked operational scripts/logs.

## Deviations
- User did not name the repository; proceeding with telegram-claude-bridge after reporting this assumption and finding its existing LG remote integration.
- Do not restart the live bridge, per AGENTS.md. Do not send unsolicited Telegram messages from verification scripts.

## Validation
- Direct client and Telegram handlers implemented; `/tv` registered as a nonblocking
  command, `tv:` callbacks precede generic callbacks, and a Panel shortcut is present.
- The legacy local HTTP relay was replaced. Plain `OK` now remains chat text;
  legacy arrows/volume still use the direct controller and report errors.
- TV state/catalog reads passed using the new controller: volume 12, YouTube,
  22 apps, 3 inputs. First read 770 ms; reused connection read 34 ms.
- Existing local pairing migrated into ignored `state/tv.json`. Reopening the
  controller from that saved file connected successfully. Wi-Fi MAC matches
  the local neighbor entry and is configured for optional Wake-on-LAN.
- Full selftest: ALL OK with an isolated ephemeral singleton port; the live
  singleton port was never released or interfered with.
- Full pytest suite: 55 passed, including 32 TV regressions and registration checks.
- New/changed Python modules compile. `git diff --check` passes.

## Handoff
- Do not claim live Telegram rollout: the running bridge has not loaded the
  Python changes. Orchestrator should restart the process after this session;
  Telegram `/restart` only restarts an agent session.
- No live navigation/audio/power commands or Telegram test messages were sent.
  Physical button effects and Wake-on-LAN still need observation after reload.
- Local state and pairing are ignored; only this feature's sources/docs/tests
  are committed. No push. Pre-existing unrelated changes remain unstaged.
