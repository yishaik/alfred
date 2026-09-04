# Agent worksheet — 2026-09-04

## Goal

Turn the proposed Manager Loop into a durable Alfred feature: a Telegram command
that plans a long task, executes phases in isolated Claude sessions, verifies
each phase against explicit acceptance criteria, and performs a final integration
review.

## Plan

1. Add a persistent orchestration state machine in its own module.
2. Add `/loop` controls for start, status, resume, and cancellation.
3. Keep execution in background tasks and preserve Alfred's normal chat session.
4. Add pure offline tests for plan parsing, state transitions, and restart recovery.
5. Update README, command registration, task queue, and example configuration.

## Decisions

- JSON is the source of truth; Telegram messages are a derived progress view.
- A phase advances only after a separate verifier returns PASS with evidence.
- Runs are serialized per working directory to avoid conflicting edits.
- An interrupted process changes RUNNING runs to PAUSED; it never silently
  resumes tool use after restart.
- Existing Claude guardrails and approval mode remain authoritative.

## Deviations

- The repository's Windows `.venv` is unavailable in this Linux checkout.
  Validation uses `python3`; the initial selftest stopped at the missing
  `claude_agent_sdk` dependency before project code executed.

## Completed

- Added `tgbridge/manager_loop.py` and persistent `manager-loops.json` state.
- Added `/loop` start/status/resume/cancel commands and Telegram progress events.
- Added independent phase verification, bounded retries, final integration gate,
  per-workdir serialization, and safe restart pausing.
- Enforced read-only tool permissions for manager, verifier, and integrator roles;
  only the implementer can mutate the repository.
- Added offline state-machine tests and user documentation.

## Validation

- `python3 -m py_compile tgbridge/*.py` — passed.
- `python -m unittest tests.test_manager_loop` in a clean temporary venv — passed.
- `git diff --check` — passed.
- Full `selftest.py` reached completion but has six pre-existing/environmental
  failures in bridge-tool metadata, singleton-port ownership, and peer protocol
  expectations. Manager Loop's dedicated tests pass independently.
