# Audit remediation — 2026-10-23

## Plan
Resolve issues #36–#49 in separate tested PRs and merge in dependency order.

## Deviations
User explicitly authorized push, separate PRs and ordered merges; supersedes local one-commit/no-push guidance. Use available POSIX Python instead of Windows venv. Never restart the live bridge or change credentials.

## Completed
- #48: Safe manual sender import, pytest discovery, registration mock, portable dev dependencies and offline CI. Validation: full pytest and compile checks.
