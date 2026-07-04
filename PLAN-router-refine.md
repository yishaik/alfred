# Router prompt-refinement stage — plan (2026-07-05)

Executor: an Opus agent working INSIDE the live bridge repo (D:\Projects\telegram-claude-bridge).
⚠️ RULES (same as prior runs):
1. Do NOT restart the bridge / kill python / run restart.ps1 — the orchestrator restarts after you finish.
2. Don't modify existing tokens in .env.
3. After each file change: `.venv/Scripts/python.exe -m py_compile <file>`.
4. One git commit at the end, NO push.
5. Line numbers below verified 2026-07-05 but may drift — re-locate before editing.
6. Fail-safe is sacred: any error/empty/degenerate refinement → send the ORIGINAL text. A refinement bug must NEVER drop or corrupt a message.

## Goal
Add a stage to the router that runs BEFORE model selection is finalized: for CLAUDE-bound, **task-shaped** prompts, decide whether to REWRITE the prompt into a better-structured one following the Second-Brain guidance on prompt-writing and loops, then **send the refined prompt to Claude AND show the user the refined version** (tagged ✍️). Trivial/chat/free-path prompts pass through untouched. User override `!raw <prompt>` skips refinement for one message.

**Decided UX (user chose):** auto-send the refined prompt; show it tagged. NOT confirm-by-button, NOT silent.

## Second-Brain grounding (the rubric source)
The rewrite must be grounded in these wiki pages (read from disk, they already exist):
- `D:\Projects\second-brain\wiki\Loop Engineering.md`
- `D:\Projects\second-brain\wiki\Agentic Engineering Concepts.md`
- `D:\Projects\second-brain\wiki\AI Agents.md`

Distilled rubric (already extracted — embed this AS the fallback, and prefer the live files):
- **Goal + definition of done:** state the objective and a concrete, checkable success criterion.
- **Context, tight:** name the relevant files/paths/constraints the agent needs; context is a budget, not a bucket — don't bloat.
- **Self-verification:** ask the agent to check its own work with a real check (run tests / a command / observe output) before claiming done — "task complete is a claim, not proof".
- **Scope + stop conditions:** for anything repetitive, bound it (max scope, what "enough" means).
- **Plan-before, log-after** for multi-step work.
- **Preserve intent & language** (usually Hebrew). RESTRUCTURE, never add scope the user didn't ask for, never invent requirements (like career-ops "reformulate, never fabricate"). Keep it concise.

## Current plumbing (verified 2026-07-05)
- `router.py`: `Decision` dataclass has `.text` (prefix-stripped text to actually use). `_heuristic()` (~187) handles forced prefixes `!c/!f/!opus/…` and returns firm Decisions. `classify()` (~325) = heuristic → `_llm_classify` (~290) → failsafe. `_chat(base_url, key, model, msgs, timeout, max_tokens, temperature)` is the httpx OpenAI-compat call. `answer_free` (~433) walks the provider chain. `load_config()`/`save_config()` (~115). Providers now include gemini/gemma-google/openrouter/groq/ollama (+ inactive nvidia/cerebras).
- `session.py _maybe_route()` (~500): classifies; on claude route sets `_turn_model` (full mode), calls `router.log_claude(...)`, returns `decision.text` (→ Claude). On free route calls `answer_free`, emits `🎈 …`, returns None.
- The action-verb heuristic (`_ACTION_EN`/`_ACTION_HE`, ~159) already identifies task-shaped prompts — reuse it as the primary "is this task-shaped?" signal.

## Phase R — refine core (router.py)

**R1. `!raw` override.** In `_heuristic`, add alongside the other forced prefixes: `!raw` / `!r` → `Decision("claude", "", "other", "forced raw (no refine)", "forced", rest)` and set a marker so refinement is skipped. Simplest: add a field `refine_skip: bool = False` to `Decision`; set True for the `!raw` case (and always True for `source=forced` free/claude? no — only `!raw`). Strip the prefix from text as the others do.

**R2. Rubric loader.** `_load_refine_rubric() -> str`: read the 3 wiki files above; concatenate their `## Key points` sections (or whole file if that heading absent), trim to ~3500 chars total; cache in a module global keyed by max mtime of the 3 files (re-read only if a file changed). If the dir/files are missing or unreadable → return the embedded distilled rubric (above). Never raises.

**R3. `should_refine(text, decision, cfg) -> bool`.** True only when ALL hold: `cfg.refine["enabled"]`; mode != "off"; `decision.route == "claude"`; not `decision.refine_skip`; `decision.source != "forced"` (don't refine slash/`!c` etc. — but DO allow normal claude/heuristic/llm); `len(text) >= cfg.refine["min_chars"]` (default 40); text has no `[received file`/`[replying to:` markers and doesn't start with `/`. Mode "always" → skip the task-shaped test; mode "auto" (default) → additionally require task-shaped = (`_ACTION_EN`/`_ACTION_HE` match OR `_DRIVE_PATH` OR ``` fence OR len>300). This keeps one-line questions/chat that happened to route to claude from being rewritten.

**R4. `refine(text, session, cfg) -> tuple[str,bool] | None`.** Never raises.
- System prompt: the rubric + strict instructions: "Rewrite the user's request into a clearer, better-structured prompt for a capable coding agent, following the principles above. PRESERVE the original intent and language exactly (usually Hebrew). Do NOT add requirements, scope, or facts the user did not state — only restructure, clarify, and make the success criterion explicit. Be concise. Output ONLY the rewritten prompt text — no preamble, no explanation, no markdown fences, no ‘Here is…’."
- Call a free model: use `cfg.refine["model"]` provider from the chain (default "gemini"); look it up in `cfg.providers` for base_url/model/env_key; fall back through the chain (gemini→gemma-google→groq→ollama) skipping missing-key/rpd-spent, same guards as answer_free. timeout 20s, max_tokens ~700, temperature 0.3. Strip leaked `<thought>` blocks (reuse `_strip_reasoning`).
- Guards → return None (use original): empty; == original after strip; starts with a refusal/meta ("here is", "הנה", "```"); longer than 4× the original chars OR > 3000 chars (ballooned = probably added scope); shorter than 50% of a >200-char original (probably truncated). Otherwise return `(refined, True)`.
- On success bump usage for the provider used, and `_log_decision` with a `refined=True` marker/reason.

**R5. Config.** Add to `RouterConfig` + `router.json` defaults a `refine` block:
```
"refine": {"enabled": true, "mode": "auto", "model": "gemini", "min_chars": 40, "show": true}
```
`RouterConfig.from_dict`/`to_dict` must round-trip it. mode ∈ off|auto|always.

## Phase S — hook (session.py `_maybe_route`)
On the claude branch, AFTER computing `_turn_model` and BEFORE `return decision.text`:
```
final = decision.text
try:
    cfg = router.load_config()
    if router.should_refine(final, decision, cfg):
        res = await router.refine(final, self, cfg)
        if res:
            refined, changed = res
            if changed:
                if cfg.refine.get("show", True):
                    self.outbox.emit(f"✍️ ניסחתי מחדש:\n{refined}")
                final = refined
except Exception as e:
    log.warning("refine failed, using original: %s", e)
router.log_claude(self, final, decision)   # log the text actually sent
return final
```
(Keep everything inside the existing try/except fail-safe of feed(); refine's own try/except is belt-and-suspenders.) Do NOT refine on the free route.

## Phase U — UX
**U1. `/router` card:** add a line `✍️ ניסוח מחדש: <mode>` and inline buttons to cycle refine mode (callbacks `rt:refine:off` / `rt:refine:auto` / `rt:refine:always`) + a show on/off toggle (`rt:refshow:0|1`). Wire the callback handler next to the existing `rt:mode:*` handler in handlers.py.
**U2. Help:** mention `!raw` (skip rewrite once) wherever `!c`/`!f` are documented (cmd_start / /router card).

## Verification (all must pass before commit)
1. `py_compile` every touched file; `.venv/Scripts/python.exe selftest.py` — the only allowed failure is the pre-existing `singleton lock acquired` (live bridge holds :49517).
2. selftest additions:
   - `should_refine`: task-shaped HE ("תבנה סקריפט שמסכם קבצים") → True; short chat ("מה שלומך") → False; `!raw …` decision (refine_skip) → False; slash/bracket → False; mode off → False; mode always makes a short claude prompt True.
   - rubric loader returns non-empty from disk AND returns embedded fallback when pointed at a missing dir (monkeypatch the path).
   - refine guards: mock `_chat` to return "```\nx\n```"/"here is the prompt: …"/a 10×-long string → refine returns None each; a clean restructured string → returns (text, True).
   - fail-safe: monkeypatch `refine` to raise → `_maybe_route` still returns the original text (message reaches Claude).
3. LIVE smoke (real free-model call, cheap): `refine("תבנה לי סקריפט פייתון שסופר כמה פעמים כל מילה מופיעה בקובץ טקסט", …)` via the real chain — assert it returns changed=True, output is Hebrew, ≤4× length, contains no code fences / no "הנה". Print the before/after.
4. grep: no bare `except Exception: pass` introduced.
5. Single commit: `router: prompt-refinement stage grounded in Second-Brain loop/prompt guides (auto-rewrite task prompts before Claude, show ✍️, !raw to skip, /router toggle)` + Co-Authored-By: Claude Opus 4.8 <noreply@anthropic.com>. NO push, NO restart.

## Out of scope
- Refining free-route or scheduler/peer/bot turns (only user→claude task prompts).
- Confirm-by-button flow (user chose auto-send + show).
- Editing the Second-Brain files.
- Any change to the model picker / provider protocol.

## Report back
tasks done/skipped; each verification check pass/fail with 1-line evidence; the live before/after refinement text; commit hash; files + line counts; any deviations and why.
