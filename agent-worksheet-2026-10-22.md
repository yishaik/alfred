# Model selection and conversation handoff

## Plan
- Discover selectable models from installed providers instead of dated IDs.
- Keep Codex threads across model changes; isolate native threads by session route.
- Persist shared conversation history and pass missed exchanges between engines.
- Serialize turns and reject configuration changes while a turn runs.
- Run offline regression tests; commit locally without restarting or pushing.

## Baseline
- Existing edits in restart.ps1, selftest.py, bridgetools.py, router.py,
  codex_engine.py and session.py predate this work and must be preserved.
- Full selftest fails in Napkin: missing @shift-labs/ferrosearch-win32-x64-msvc.
- Snapshot of overlapping files: ignored state/model-handoff-before/.

## Deviations
- Full selftest cannot pass on this Windows installation: the Napkin native
  Windows module is missing and its exact npm package returns 404. No unrelated
  memory subsystem changes were made to bypass this failure.
- Full pytest: 70 passed, 1 failed. The failing TV command registration test
  also fails at original HEAD e7ac1ff in an isolated worktree at
  D:/Projects/.work/alfred-baseline-20261022. main.py was not changed.
- Existing related session-history changes and Codex error parsing are retained
  in the selected session.py/codex_engine.py commit. Unrelated dirty files remain
  outside the commit, including selftest.py, router.py and watchdog changes.

## Result
- Codex model changes retain the thread and pass -m on resume. Default uses the
  configured model when present. Native external threads/models persist by route.
- Provider discovery replaces dated Claude IDs. Codex model/list was checked live:
  seven visible models, with display labels. No inference turn was submitted.
- Shared local journal saves complete exchanges; only successful turns advance
  handoff cursors. Existing Claude history bootstraps once. Prompt history is
  bounded with explicit omissions and the journal path.
- External turns, router turns and Claude use one queue. Busy model/engine
  changes are refused. Failed providers fall through to Claude with context.
- Sixteen new offline tests pass. Existing targeted collect/router/failover
  selftest checks pass. Changed Python modules compile.
- See docs/model-handoff.md for operation, storage and limits.

## Activation
- No push, live bridge restart, Telegram test message or inference call performed.
- Orchestrator must activate via restart, then verify /model and a live
  Claude -> Codex -> Claude exchange. Antigravity device/runtime behavior and
  live Claude model discovery remain unverified.

## Follow-up: Engine panel
- User requested that the panel Engine button switch providers properly.
- Baseline: sixteen handoff tests pass; full selftest still has the documented
  Napkin environment blocker.
- Plan: replace prompt forwarding with a session-bound provider picker, share
  command/button transition logic, retain context and model pins, and cover
  stale panels, busy sessions, availability failures and legacy buttons offline.
- Implemented: Engine picker, provider readiness and active model, direct
  transitions shared with /engine, and session-bound follow-on model selection.
  Existing send:/engine panels are handled without passing text to any model.
- Validation: 13 new panel tests and 16 existing handoff tests pass. Full pytest:
  83 passed, same pre-existing TV registration failure. No live bridge restart,
  push, provider inference or Telegram message was performed.
