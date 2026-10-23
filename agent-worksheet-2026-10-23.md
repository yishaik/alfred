# Audit remediation — 2026-10-23

## Plan
Resolve issues #36–#49 in separate tested PRs and merge in dependency order.

## Deviations
User explicitly authorized push, separate PRs and ordered merges; supersedes local one-commit/no-push guidance. Use available POSIX Python instead of Windows venv. Never restart the live bridge or change credentials.

## Current repair pass
- Reproduced the Windows Napkin failure: `@shiftlabs/ferrosearch@0.1.1` is
  referenced by the installed package but that version is not available from
  npm, leaving `@shift-labs/ferrosearch-win32-x64-msvc` missing.
- Added a local markdown fallback in `tgbridge/memory.py`. Pinned memory and
  note CRUD/search continue to work when Napkin cannot initialise.
- Updated the singleton self-test to report a skip when the live bridge owns
  the lock port. This avoids treating the running production instance as a
  self-test failure.
- Verified `pytest -q`: 129 passed. Verified `selftest.py`: ALL OK.
- Backup repo `D:\Projects\alfred-state-backup` is 56 commits ahead and 121
  behind `origin/main`. No merge or push was performed; this needs a separate
  controlled reconciliation.

## Completed
- #48: Safe manual sender import, pytest discovery, registration mock, portable dev dependencies and offline CI. Validation: full pytest and compile checks.

- [x] #49: missing optional Napkin integration is explicitly skipped; mocked transport success/failure and vault isolation are covered. Full pytest and selftest pass.

- [x] #36: dynamic picker separates OpenRouter provider from model ID; paid opt-in and Claude fallback have regression coverage.

- [x] #38: cancel/timeout/stop reap Codex process trees; intentional cancellation never replays to Claude. Real local subprocess regression tests added.

- [x] #37: Telegram submits owned background tasks while feed/collect remain awaitable; FIFO, prompt responsiveness and shutdown cancellation tested.

- [x] #39: Codex exports agent, route and state directory to MCP; RPC resolves exact session and rejects unknown/mismatched identities. Legacy known-agent callers remain supported.

- [x] #40: RPC calls the shared session permission gate; explicit read-only bridge tools remain usable; writes require approval or auto-approval and are always denied for read-only roles.

- [x] #41: rejected scheduled turns retain jobs with bounded exponential retry, including recurring occurrences; accepted one-shots are removed.

- [x] #42: early Telegram authorization gate validates chat and human actor for every update; owner default, optional explicit delegates, anonymous/bot rejection documented and tested.

- [x] #43: shared CODEX_HOME resolver for auth, config and model cache; custom-home test uses synthetic credentials only.

- [x] #44: explicit external answers use bounded durable handoff and retain the full returned text; UI accurately discloses bounded history; automatic quick-reply limits retained.

- [x] #45: /model clears paid one-shot selection before catalog discovery; regression covers cancellation order.

- [x] #46: model keyboards read cache only; discovery runs in a bounded worker thread, coalesces concurrent callers and retains cached results on failure. Unverified fallback IDs removed.

- [x] #47: OpenAI transcription failure falls back to configured Groq; success stops the chain; missing credentials never trigger a request.

- [x] #64: explicit one-shot selection works while Codex is active, uses real HTTP adapter under MockTransport and preserves native engine. Routed collect results resolve capture futures without losing FIFO.

- [x] #65: read-only native engine switching/failover fails closed; SDK readonly options cannot bypass permissions and exclude write/shell/delegation tools. Added Linux/Windows/macOS CI matrix.
