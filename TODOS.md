# Alfred — Task Queue

Consolidated from PLAN-*.md files. Agent: work top-to-bottom; one task at a time; update status here.

## Status legend
- `[ ]` open · `[~]` in progress · `[x]` done · `[-]` dropped

---

## 🔴 High priority

- [ ] **Pre-commit hook** — add `.git/hooks/pre-commit` that runs `py_compile` on all changed .py files; fail on syntax error
- [ ] **7-line summaries** — add greppable header block to each tgbridge/*.py module (name, purpose, inputs, outputs, key functions, dependencies, last-updated)

---

## 🟡 Medium priority (from PLAN-stability-ux.md 2026-07-03)

- [~] **A1. Serialize state writes** — add threading.Lock inside config.save_json to prevent race on Windows
- [ ] **A2. Graceful shutdown** — wrap m.stop_all() in asyncio.wait_for(timeout=30) in main.py post_shutdown
- [ ] **UX: queue position display** — show position + "🗑 Clear queue" button for queued messages
- [ ] **Scheduler: topic-aware fire** — jobs created in a forum topic fire back into that topic

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
