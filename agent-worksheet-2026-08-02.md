# Agent worksheet — 2026-08-02

## Goal

Add automatic engine failover when the active Claude or Codex subscription can
no longer accept turns because its usage/quota limit has been exhausted.

## Plan

1. Detect explicit quota/usage exhaustion without treating ordinary failures as exhaustion.
2. Replay a rejected Claude turn through Codex and make Codex the active engine.
3. When Codex reports quota exhaustion, replay through the existing Claude path and make Claude active.
4. Add offline regression coverage, compile all changed Python, and run `selftest.py`.
5. Document the behavior in `TODOS.md`, then commit once without pushing.

## Baseline

`selftest.py` completed with 6 pre-existing failures: three bridge-tool metadata
checks, singleton-lock acquisition, and two peer-protocol checks. The remaining
checks passed. These failures existed before this feature's edits.

## Decisions

- A hard Claude `RateLimitEvent(status="rejected")` is authoritative.
- Text matching is used only for Codex CLI errors and Claude result errors, with
  narrow quota/usage phrases and HTTP 429; generic network/process errors do not
  permanently switch engines.
- An automatic switch is per live session, matching `/engine`; it is deliberately
  not written to agent configuration across bridge restarts.
- Ordinary Codex errors retain the existing one-turn fallback to Claude.

## Deviations

- The repository instructions show Windows `.venv/Scripts/python.exe` commands,
  but this checkout is running on Linux. The equivalent `.venv/bin/python` is used.

## Result

Implemented bidirectional usage-exhaustion failover. Claude's authoritative
`RateLimitEvent(status="rejected")` and narrow result-error matching trigger a
replay through Codex; Codex quota errors use the existing Claude fallback and
also change the live engine back to Claude.

The changed modules and self-test compile. The full offline suite has the same
6 failures as the baseline and no new failures; all 9 new failover assertions
pass.
