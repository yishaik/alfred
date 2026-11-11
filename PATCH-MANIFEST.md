# Overlay Manifest

Copy every file in this directory to the same relative path in `yishaik/alfred`.
No existing Alfred source file is replaced. The integration relies on:

- `Dockerfile.fly` copying the current repository
- `cloud/entrypoint.sh` moving runtime `state/` to `/data/state` and symlinking it back
- global Claude instructions under `/data/home/.claude/CLAUDE.md`
- the existing Alfred Claude Agent SDK retaining primary execution, Telegram approvals, audit, scheduling, memory, and guardrails

This minimizes regression risk and lets the feature land as an additive PR.
