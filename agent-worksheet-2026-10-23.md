# Audit remediation — 2026-10-23

## Plan
Resolve issues #36–#49 in separate tested PRs and merge in dependency order.

## Deviations
User explicitly authorized push, separate PRs and ordered merges; supersedes local one-commit/no-push guidance. Use available POSIX Python instead of Windows venv. Never restart the live bridge or change credentials.

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
