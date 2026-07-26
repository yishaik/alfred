# Model router + free-model backends — plan (2026-07-04)

Executor: an Opus agent working INSIDE the live bridge repo (D:\Projects\telegram-claude-bridge).
⚠️ RULES (same as the hardening run):
1. Do NOT restart the bridge — the orchestrator restarts after you finish (a restart kills your own process).
2. Don't modify existing tokens in .env; you MAY append new keys (task E1 says which).
3. After each file change: `.venv/Scripts/python.exe -m py_compile <file>`.
4. One git commit at the end, NO push.
5. Line numbers below were verified 2026-07-04 but may drift — re-locate before editing.
6. The router must NEVER lose or block a message: any exception in router code → log.warning → fall through to the Claude session exactly as today (fail-safe = current behavior).

## Goal
Before every USER prompt, a router decides the best executor:
- **free path** — trivial/self-contained asks (quick Q&A, translation, define, summarize pasted text, small talk) get answered by a FREE model (local Ollama or a free cloud tier), bypassing the Claude session entirely. Saves Anthropic tokens; instant answers even while Claude is busy.
- **claude path** — everything else goes to the Claude session as today; in `full` mode the router may also pick the Anthropic tier (haiku/sonnet/opus) per prompt for agents whose model is "" (auto).

## Current plumbing (verified)
- `handlers.py:1062 on_text` → `session.feed(text, TurnSource())` (session.py:454) → `_send_turn` (:502) → `client.query()`. `feed()` is the single funnel (also media captions, voice, scheduler, peers — distinguishable via `source.kind`).
- Live model switching already works: handlers.py:1298–1304 (`s.cfg.model = model; await s.client.set_model(model or None)`).
- `.env` already has `GROQ_API_KEY` (set). Bridge venv has `httpx` 0.28.1 (via PTB). Local Ollama models: gemma4:e4b, qwen2.5:14b, qwen2.5:7b, glm-4.7-flash.
- Existing MCP tool `route_model` (bridgetools.py:219) is ADVISORY (x-reader route.mjs) — leave untouched; unrelated feature.
- All state writes must go through `config.save_json` (it holds the write lock — added in the hardening pass).

## Research findings (verified 2026-07-04, web)
Fallback-chain building blocks; full provider notes at bottom.
- **Classifier (every message → must be fast + unlimited):** local Ollama `gemma4:e4b` primary (~100–300 ms, zero limits) → Groq `llama-3.1-8b-instant` (30 RPM / 14,400 RPD, ~840 tok/s) → pure heuristics.
- **Hebrew answer quality ranking (free):** Gemini 3 Flash > GPT-4o-class (GitHub Models) > Gemma-4-31B > gpt-oss-120b > Qwen3 > Llama 3.x (Hebrew NOT in Llama's official languages — avoid for Hebrew OUTPUT; fine for classification).
- **Answer chain (chat/translate/summarize):** Gemini 3 Flash (OpenAI-compat: https://generativelanguage.googleapis.com/v1beta/openai/, GEMINI_API_KEY, ~10 RPM / ~250–1500 RPD, 1M ctx, best Hebrew) → OpenRouter `google/gemma-4-31b-it:free` (https://openrouter.ai/api/v1, OPENROUTER_API_KEY, 20 RPM; 50 req/DAY until a one-time $10 credit purchase lifts it to 1000/day) → Groq `openai/gpt-oss-120b` (https://api.groq.com/openai/v1, 30 RPM / 1K RPD / 8K TPM — short tasks only) → local Ollama `qwen2.5:14b` (unlimited, last resort before Claude).
- Skipped as primaries: Cerebras (5 RPM), GitHub Models (8K-in/4K-out hard cap, 50–150 RPD), Mistral free (phone-verify + weak Hebrew), SambaNova (20 RPD), Z.ai GLM (1 concurrent, CN privacy), Cohere (1K/month). Any of these can be added later via router.json without code changes (config-driven provider list).
- Privacy: Google free tier TRAINS on prompts; OpenRouter :free may route to training providers; Groq does NOT train. Personal-bot OK; the free path never sees file contents or tool output by design (it only gets the user's message + tiny rolling history).

## Phase R — router core

**R1. New module `tgbridge/router.py`.**
- `RouterConfig` loaded from `state/router.json` via config helpers (create with defaults on first load, save through `save_json`). Fields: `enabled` (default true), `mode`: `off` | `free_only` | `full` (default `free_only`), `tag_replies` (default true), `per_agent`: {name: {enabled, mode}} overrides, `providers`: ordered list of {name, base_url, model, env_key, rpm, rpd, max_chars} — defaults per the research chain above (ollama entries use base_url http://127.0.0.1:11434/v1, no key), `classifier`: {ollama_model: "gemma4:e4b", groq_model: "llama-3.1-8b-instant", timeout_s: 4}.
- Env kill switch: `BRIDGE_ROUTER=0` disables everything regardless of JSON.
- `classify(text, session) -> Decision` where Decision = {route: "free"|"claude", tier: ""|"haiku"|"claude-sonnet-5"|"opus", task: "chat"|"translate"|"summarize"|"other", reason, source: "heuristic"|"llm"|"forced"|"failsafe"}.
  - Heuristic FAST-PATH (no LLM call), in order:
    1. Forced prefixes: `!c`/`!claude` → claude; `!f`/`!free` → free; `!opus`/`!sonnet`/`!haiku`/`!fable` → claude + that tier. Strip the prefix from the text.
    2. `source.kind != "user"` → claude (scheduler/peer/bot turns are never free-routed — enforce in the feed hook, not only here).
    3. Text starts with "/" or contains bracket-context markers `[received file`, `[replying to:` with file refs → claude.
    4. Session has pending questions (`s.questions` non-empty with undone futures) or the previous assistant turn ended with "?" → claude (mid-dialog answers must reach Claude).
    5. Obvious action verbs / repo signals (EN: build, fix, run, commit, push, deploy, install, create file, refactor, test; HE: תבנה, תתקן, תריץ, תבדוק, תדחוף, תפרוס, תתקין, צור, תוסיף, תשנה, קומיט; plus any workdir/project-name token from agents.json, drive-letter paths, ``` fences) → claude.
    6. Very long input (> 2000 chars) that is NOT prefixed by a summarize-verb → claude.
  - Otherwise → LLM classification: ONE call, strict-JSON prompt (`{"route":"free|claude","task":"...","tier":"light|medium|heavy"}`), first via local Ollama (`/v1/chat/completions`, timeout 4 s), fallback Groq 8b-instant (timeout 4 s). Both fail → Decision(claude, source="failsafe"). Prompt must say: "If the request needs tools, files, code, memory of the ongoing project, or actions on the user's machine → claude. Only pure-text self-contained asks → free. When unsure → claude." Include the last 2 exchanges from the rolling history for context.
- `answer_free(text, session, decision) -> str | None`: walk the provider chain (skip providers whose env key is missing/empty or whose rpd budget is spent), OpenAI-compat `/chat/completions` via httpx (per-provider timeout 20 s, total budget 45 s). System prompt: "You are the quick-reply sidekick of a Telegram assistant. Answer briefly (≤1500 chars) in the user's language (usually Hebrew). If the task actually needs tools, files, code, or the main assistant's project memory, reply with exactly ROUTE_TO_CLAUDE." Include rolling history (last 8 msgs). If the reply is `ROUTE_TO_CLAUDE` (or empty) → return None (caller falls through to Claude).
- Usage accounting: `state/router-usage.json` {date, {provider: count}} — reset when date changes; write via save_json. In-memory RPM throttle per provider (simple timestamp deque) — if throttled, skip to next provider.
- Decision log: append one JSON line per routed message to `state/router-log.jsonl` (ts, agent, chars, route, task, provider?, latency_ms, ok, reason). Plain append (single writer per event loop is fine); rotate: if > 2 MB, truncate to last 500 lines.

**R2. Hook into `session.feed()` (session.py:454).**
At the top of `feed()`, before the busy/queue logic, for `source.kind == "user"` only, when router enabled for this agent:
- `decision = await router.classify(text, self)` (wrap EVERYTHING in try/except → on error proceed unrouted).
- If `decision.route == "free"`: `self._react("👀")`; `ans = await router.answer_free(...)`. On success: emit via `self.outbox.emit(f"🎈 {provider_short} · {ans}")` when tag_replies else plain; record the exchange in `self.free_history` (deque maxlen 8) AND `self.free_notes` (list); log; **return True without touching the Claude session** (works even when busy — that's a feature: instant answers while Claude grinds). On None/failure: fall through to the claude path below.
- Claude path: if `decision.tier` and mode==full and `self.cfg.model == ""` (agent on auto): remember `self._turn_model = tier`. In `_send_turn`, if `_turn_model` differs from the last one applied → `await self.client.set_model(tier or None)` before `query()` (guard with try/except; on failure just proceed). Do NOT touch `cfg.model` (persistent per-agent pin stays authoritative; pinned agents are never tier-routed).
- Free-context handoff: in `_send_turn`, if `self.free_notes` non-empty → prefix the outgoing text with `[FYI — quick side-model exchanges since your last turn: Q:… → A:… (truncated)]` (each note ≤200 chars, max 5) and clear the list. This keeps Claude's world consistent.
- Queued turns: `pending` tuples stay `(text, source)`; queued messages are re-classified? NO — a queued message was already decided claude (free ones return early), keep as-is.

**R3. Free-history plumbing.** `AgentSession.__init__`: add `self.free_history: deque = deque(maxlen=8)`, `self.free_notes: list = []`, `self._turn_model: str = ""`. Claude turns should ALSO append (user_text, final_reply≤300 chars) to `free_history` so the free model has real context — hook where the final assistant text is already known (the `_consume`/capture completion path; find where the turn's final text is assembled).

## Phase U — UX

**U1. `/router` command.** Status card: mode, classifier health (ping Ollama with 1-token call, show ✓/✗), today's usage per provider, last 3 decisions. Inline buttons: mode off / free_only / full; tag on/off (callbacks `rt:mode:<m>`, `rt:tag:<0|1>`). Register in COMMANDS + `BOT_COMMANDS` in main.py (Hebrew desc: "ראוטר מודלים — מצב ושליטה").

**U2. `/routes` command.** Last 10 lines of router-log.jsonl, human-formatted, phone-friendly (`🎈 free·gemini 1.2s "מה זה..."` / `🧠 claude·opus "תבנה..."`).

**U3. Reply tagging.** Free-path replies prefixed `🎈 <short> · ` (gemini/gemma/groq/local). `_pretty_model` in session.py: add mappings for the new tier ids so /status shows them nicely.

**U4. Panel.** Add a `🎈 Router` row to /panel opening the /router card (reuse the same callback).

## Phase E — env & config

**E1. Keys.** APPEND to `.env` (do not touch existing lines): `GEMINI_API_KEY=<copy the value from D:\Projects\clip-factory\.env — key name GEMINI_API_KEY or GOOGLE_API_KEY, whichever is set there>` and `OPENROUTER_API_KEY=` (empty placeholder; provider auto-skipped while empty). If clip-factory has no Gemini key, leave `GEMINI_API_KEY=` empty and note it in the commit message body.

**E2. Defaults.** `state/router.json` created on first run with: enabled=true, mode=`free_only`, tag_replies=true, provider chain = [ollama-classify is separate] answers: gemini-flash → openrouter gemma-4-31b-it:free → groq gpt-oss-120b → ollama qwen2.5:14b; budgets: gemini 200/day, openrouter 40/day, groq 500/day, ollama unlimited.

## Verification (all must pass before commit)
1. `py_compile` every touched file + `python selftest.py` passes.
2. New `selftest` additions: (a) heuristic classify cases — "תבנה לי סקריפט"→claude, "מה בירת צרפת"→free-eligible (heuristics pass it to LLM stage; with router mocked/disabled it must NOT crash), "!c מה השעה"→claude forced, text with `D:\path`→claude, pending-question session→claude; (b) provider skip logic when env key missing; (c) usage-file date rollover.
3. LIVE smoke (real calls, cheap): one `answer_free("מה בירת צרפת?")` through the real chain — assert non-empty Hebrew-ish answer and correct usage increment; one classifier call against local Ollama — assert valid JSON parse. If Ollama daemon is down, note it and rely on the Groq fallback test instead.
4. grep: no bare `except Exception: pass` introduced.
5. Confirm feed() fail-safe: temporarily monkeypatch router.classify to raise in a unit test → message still reaches `_send_turn`.
6. Single commit: `router: pre-prompt model router + free-model backends (local Ollama/Gemini/OpenRouter/Groq), /router+/routes UX` + Co-Authored-By trailer. NO push, NO restart.

## Out of scope
- Routing scheduled/bot/peer turns (always claude).
- Free models with tool use — never; free path is text-only by design.
- Buying OpenRouter credit ($10 one-time lifts 50→1000 req/day) — recommend to the user, their call.
- Touching the advisory `route_model` MCP tool or x-reader's route.mjs.
