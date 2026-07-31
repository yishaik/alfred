# Alfred — Task Queue

Consolidated from PLAN-*.md files. Agent: work top-to-bottom; one task at a time; update status here.

## Status legend
- `[ ]` open · `[~]` in progress · `[x]` done · `[-]` dropped

---

## 🔴 High priority

*(nothing open — see Done 2026-07-31)*

---

## 🟡 Medium priority (from PLAN-stability-ux.md 2026-07-03)

*(nothing open — A1/A2 and both UX items are done; see Done 2026-07-31)*

---

## 🟡 Medium priority (from PLAN-model-router.md 2026-07-04)

- [ ] **Router: OpenRouter integration** — all non-Claude models route through existing OpenRouter key
- [ ] **Router: fail-safe** — any exception in router → log.warning → fall through to Claude session
- [ ] **Free backends** — wire in free-tier models (Gemini, Mistral, etc.) as router targets

---

## 🟡 Medium priority (from PLAN-router-refine.md 2026-07-05)

- [ ] **Router prompt refinement** — improve routing heuristics; fail-safe: empty/degenerate refinement → send original
- [ ] **Refinement audit** — log all router decisions to audit.jsonl for /audit review

---

## 🟡 Medium priority (from PLAN-arena-models.md 2026-07-08)

- [ ] **Arena top-10 models in /model picker** — expose 7 unique models (Anthropic via session path, rest via OpenRouter)
- [ ] **Route A confirmed** — external models via OpenRouter; Claude via Claude Code session (set_model)

---

## 🟢 Low priority / backlog

- [ ] **Cross-agent review skill** — doc for asking Codex/Cursor to review Alfred code from a different angle
- [ ] **Coding conventions doc** — Alfred-specific: async patterns, error handling, state write rules
- [ ] **Periodic commit sweep** — skill to scan recent commits for gotchas / regressions
- [ ] **False-confidence test audit** — check selftest.py for tests that pass but don't test what they claim
- [ ] **Performance benchmarks** — baseline latency for message round-trip (send → first token → complete)
- [ ] **End-of-shift validation** — run selftest + audit + log scan before ending any autonomous session

---

## ✅ Done (recent)
- [x] supervisor.py crash-loop backoff + log rotation
- [x] Daily state backup to state/backup/
- [x] /audit command (audit.jsonl)
- [x] Queue control (position display, clear button)
- [x] Scheduler: topic-aware job firing
- [x] Error counters in /status
- [x] AGENTS.md router — 2026-07-12
- [x] AGENT_WORKFLOW.md — 2026-07-12
- [x] **Pre-commit hook** — tools/pre-commit (py_compile on the STAGED content of
      changed .py files); install with tools/install-hooks.sh — 2026-07-31
- [x] **7-line summaries** — every tgbridge/*.py carries the block; `grep '^Purpose:'
      tgbridge/*.py` lists them — 2026-07-31
- [x] **A1. Serialize state writes** — config._SAVE_LOCK; save_json is the single
      writer for every state file — 2026-07-31 (verified)
- [x] **A2. Graceful shutdown** — main.post_shutdown already wraps stop_all() in
      asyncio.wait_for(30) — 2026-07-31 (verified, was stale in this list)
- [x] **Stale resume self-heal** — a session id the CLI doesn't know (state restored
      onto another machine) is dropped and restarted fresh instead of failing every
      start with "exit code 1" — 2026-07-31
- [x] **Surface the CLI's stderr** — start/restart/send failures now carry the claude
      subprocess's last stderr lines into the log and the chat — 2026-07-31
- [x] **Conflict give-up** — a getUpdates conflict lasting >10 min concedes the bot
      token and exits rc=75; supervisor waits 15 min — 2026-07-31
- [x] **Gentler restart ladder** — supervisor 5/5/15/30/60/300s, so a DNS blip no
      longer costs 5 minutes offline — 2026-07-31
