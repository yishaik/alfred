# Add the Agent-Arena top models to the bridge (picker + router) — plan (2026-07-08)

Executor: an Opus agent inside the LIVE bridge repo (D:\Projects\telegram-claude-bridge).
⚠️ RULES: (1) Do NOT restart the bridge / kill python / run restart.ps1 — the orchestrator restarts after you finish. (2) Don't modify existing .env token VALUES; you MAY read them and MAY append the two config placeholders in E1 if needed. (3) After each file change: `.venv/Scripts/python.exe -m py_compile <file>`. (4) One git commit at the end, NO push. (5) Line numbers drift — re-locate before editing. (6) Fail-safe: any router/provider error → fall through to the current behavior (Claude), never drop a message.

## Goal
Make the Agent-Arena top-10 models usable from the bridge, exposed BOTH in the `/model` picker AND as router targets. The user chose **Route A: everything external goes through the existing OpenRouter key** (already set in OS env, has credit, approved). Claude models run through the existing Claude Code subscription session (NOT OpenRouter).

### The models (10 arena entries → 7 unique; effort suffixes like (High)/(Thinking)/(xHigh) are NOT separate models)
Anthropic — via the existing Claude Code **session** path (set_model), NOT OpenRouter:
- Claude Fable 5 — `claude-fable-5`
- Claude Opus 4.8 — `claude-opus-4-8`
- Claude Opus 4.7 — `claude-opus-4-7`
- Claude Sonnet 5 — `claude-sonnet-5`

External — via **OpenRouter** (`https://openrouter.ai/api/v1`, `OPENROUTER_API_KEY`, OpenAI-compatible /chat/completions):
- GPT-5.5 — OpenRouter slug `openai/gpt-5.5`
- GPT-5.4 — OpenRouter slug `openai/gpt-5.4`
- GLM-5.2 — OpenRouter slug `z-ai/glm-5.2`
(Verify each slug with a live smoke call — see Verification. If a slug 404s, query `GET https://openrouter.ai/api/v1/models` and pick the correct id, then note the correction in the commit body.)

## Current architecture (verified 2026-07-08, re-check line numbers)
- **Session model** (`cfg.model`, session.py ~200 / handlers.py model callback ~1298): the Claude Code subprocess model. ONLY Claude IDs valid here — set via `await s.client.set_model(id or None)`. This is how the `/model` picker currently switches models. `handlers.py model_kb` (~line 80) builds the inline keyboard; the `model:<id>` callback applies it live.
- **Router providers** (router.py): external OpenAI-compat models the router calls directly via httpx for the FREE path (`answer_free`, ~line 433, walks `cfg.providers`). Config in `state/router.json`. Force-prefixes handled in `_heuristic` (~line 193): `!c/!f/!opus/!sonnet/!haiku/!fable`, plus `!raw` (refine skip). Per-provider `rpd`/`rpm` guards + usage accounting already exist.
- Non-Claude models CANNOT be the session model. So GPT/GLM are exposed as **router providers** + a **picker that routes the NEXT turn to that external model** (see U-section), not as session-model switches.

## Phase M — models & providers

**M1. router.json — add 3 OpenRouter providers.** Append to `providers` (after the existing ones), each OpenAI-compat via OpenRouter:
```
{"name":"gpt-5.5","base_url":"https://openrouter.ai/api/v1","model":"openai/gpt-5.5","env_key":"OPENROUTER_API_KEY","rpm":10,"rpd":50,"max_chars":8000},
{"name":"gpt-5.4","base_url":"https://openrouter.ai/api/v1","model":"openai/gpt-5.4","env_key":"OPENROUTER_API_KEY","rpm":10,"rpd":50,"max_chars":8000},
{"name":"glm-5.2","base_url":"https://openrouter.ai/api/v1","model":"z-ai/glm-5.2","env_key":"OPENROUTER_API_KEY","rpm":15,"rpd":200,"max_chars":8000}
```
These are the conservative daily caps (guardrail against a runaway paid loop). IMPORTANT: these must NOT silently enter the default free-answer chain and get auto-picked for ordinary chat (that would spend money). Keep the auto free-answer chain as-is (gemini→gemma→groq→ollama). The new 3 are **opt-in targets** only — reachable by explicit user selection/prefix (M2/U1), not by the auto classifier. Implement by tagging them, e.g. a `"manual": true` field on the provider, and have `answer_free`'s default walk skip `manual` providers unless a specific provider is requested.

**M2. Force-prefixes for external models** (router.py `_heuristic`): add `!gpt` / `!gpt55` → gpt-5.5; `!gpt54` → gpt-5.4; `!glm` → glm-5.2. Each strips the prefix and routes THIS turn's answer through that specific provider (free path, but pinned to the named provider — add a `Decision.provider` field or reuse `Decision.task`/a new field to carry the pinned provider name; `answer_free` honors a pinned provider by calling only that one). SHORT-CIRCUIT: a pinned external provider bypasses the classifier entirely.

**M3. `answer_free` — honor a pinned provider.** Add an optional pinned-provider path: if the decision names a provider, call only that provider (still with key/rpd/rpm guards + reasoning strip + usage accounting + logging); on failure emit a clear error and fall through to Claude (never silent).

## Phase P — picker (`/model`)
**P1. Claude models → session picker.** Ensure `model_kb` (handlers.py ~80) lists all four: Opus 4.8 (`claude-opus-4-8`), Sonnet 5 (`claude-sonnet-5`), Fable 5 (`claude-fable-5`), Opus 4.7 (`claude-opus-4-7`), + Haiku + Default/Back. These set the SESSION model live (existing `model:` callback). Fix `_pretty_model` (session.py ~52) to label `claude-sonnet-5`→"Sonnet 5", `claude-opus-4-7`→"Opus 4.7" if not already.
**P2. External models → a second picker.** Add an `/models` command (or a second keyboard section reachable from `/model` via a "🌐 חיצוניים" button) listing **GPT-5.5 / GPT-5.4 / GLM-5.2**. Tapping one sets a per-session "next-turn external model" pin (same mechanism M2 uses) so the NEXT user message is answered by that external model. Make it clear in the confirmation text that it applies to the next message (one-shot) — OR, if simpler and clearly labeled, a sticky "external mode" the user turns off with Default; pick the one-shot pin unless sticky is trivial. Callback data e.g. `xmodel:<provider>`.
Either way: Claude models switch the durable session model; external models pin the next answer. Label the distinction in the UI (Hebrew): Claude = "מודל השיחה", external = "ענה על ההודעה הבאה עם…".

## Phase U — UX / discoverability
**U1.** `/router` card + help: document the new prefixes (`!gpt`, `!gpt54`, `!glm`) and that external models are paid-per-token via OpenRouter with a daily cap. Show today's usage for the 3 new providers (usage accounting already renders per-provider).
**U2.** `/model` (or `/start` help): one line that Claude models are the conversation model (subscription) and GPT/GLM are external one-shot answers (OpenRouter, capped).

## Phase E — env/config
**E1.** No new keys needed (OPENROUTER_API_KEY already set). Do NOT add ANTHROPIC/OPENAI/ZAI keys. If `state/router.json` lacks the new providers on load, they're added by editing the file directly (it's gitignored state) AND the defaults in `router.py` (`DEFAULT_*` / RouterConfig) so a fresh config regenerates them. Edit BOTH the live `state/router.json` and the code defaults.

## Verification (all before commit)
1. `py_compile` every touched file; `.venv/Scripts/python.exe selftest.py` — only allowed failure is the pre-existing `singleton lock acquired`.
2. selftest additions: `!gpt`/`!gpt54`/`!glm` prefixes strip + pin the right provider; a pinned provider bypasses the classifier; the 3 manual providers are EXCLUDED from the default auto free-answer walk (assert a normal free-eligible prompt never selects gpt/glm); usage/rpd guard works for a manual provider.
3. LIVE smoke (real OpenRouter calls, cheap — 1 short prompt each): call each of `openai/gpt-5.5`, `openai/gpt-5.4`, `z-ai/glm-5.2` through OpenRouter `/chat/completions` and assert a non-empty answer. If any slug 404s, query the OpenRouter models list, correct the id in BOTH router.json and code defaults, re-test, and note it in the commit body. Print the before/after model ids + a snippet of each answer.
4. grep: no bare `except Exception: pass` introduced.
5. Confirm fail-safe: a pinned external provider that errors (monkeypatch _chat to raise) still falls through to Claude and doesn't drop the message.
6. Single commit: `router+picker: add Agent-Arena models — GPT-5.5/5.4 + GLM-5.2 via OpenRouter (manual/opt-in, daily-capped) and Claude Fable5/Opus4.8/4.7/Sonnet5 in the model picker` + Co-Authored-By: Claude Opus 4.8 <noreply@anthropic.com>. NO push, NO restart.

## Out of scope
- Auto-routing ordinary chat to paid GPT/GLM (they're opt-in only — cost safety).
- Adding Claude models via OpenRouter (subscription session already serves them).
- NVIDIA/Cerebras free backends (separate, not requested here).
- Changing the existing free-answer chain or the refine stage.

## Report back
tasks done/skipped; each verification check pass/fail w/ 1-line evidence; the live per-model answer snippets + any slug corrections; commit hash; files+line counts; deviations.
