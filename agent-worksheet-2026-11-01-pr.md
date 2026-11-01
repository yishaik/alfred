# Security and reliability PR — 2026-11-01

User requested a PR. The deployment branch was 23 commits ahead of GitHub;
this PR ports only the two scoped fixes onto origin/master (672778f).
The deployment checkout and running services were left unchanged.

The security port includes the existing guard hook, audit redaction and SDK
environment wiring needed by the older upstream baseline. Two focused tests
exercise the actual hook under auto-approve and the SDK options environment.
The reliability port preserves deliberate interruption without replay, queue
progress, enqueue-time topic destinations and unique long-prompt files.

Validation in an isolated worktree: 165 pytest tests pass; selftest ALL OK
with a separate singleton-lock port. Changed Python modules compile and the
diff passes whitespace checks. Tests use a nonexistent env file and temporary
state; no live bot/provider calls or credential reads were required.

Limits: enqueue-time destination capture does not implement per-turn topic
ownership for output produced after retargeting. Native-engine approval parity,
stronger secret broker/reveal authentication, uncertain-effect reconciliation,
and durable job/delivery acknowledgement are separate follow-ups. Source tests
are not evidence of deployment or live Telegram behavior. No service restart.
