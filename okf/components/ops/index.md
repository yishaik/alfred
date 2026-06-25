# Concepts

* [digest.py — daily digest & health](digest.md) - Summarizes the audit trail per agent (tool counts, costs, denials) for the daily health report.
* [escalate.py — auto-escalation alerts](escalate.md) - Edge-triggered alerts for low disk, queue backlog, and crash runs (once per condition).
* [metrics.py — event counters](metrics.md) - In-process counters (reset on restart) for /status and the daily health report.
* [supervisor.py — crash-loop supervisor](supervisor.md) - Runs bridge.py forever with exponential backoff and rotates bridge.log; no third-party deps.
* [transcripts.py — conversation search](transcripts.md) - Full-text search over local Claude Code session transcripts, scoped to an agent's workdir.
* [watchers.py — passive change watchers](watchers.md) - Polls files/dirs/git repos with cheap fingerprints and feeds the agent proactive turns on change.
