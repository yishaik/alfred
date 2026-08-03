# Alfred — Task Queue

Consolidated from PLAN-*.md files. Agent: work top-to-bottom; one task at a time; update status here.

## Status legend
- `[ ]` open · `[~]` in progress · `[x]` done · `[-]` dropped

---

## 🔵 פתוח מסשן 2026-08-01 (הקמת התיבה)

### דורש את המשתמש
- [ ] **`/restart` לאלפרד** — להצטרף ל-peer bus. שני הסוכנים כבר מדברים; אלפרד מצטרף רק אחרי אתחול. לא בוצע אוטומטית כדי לא לנתק שיחה פעילה.
- [ ] **להחליף את טוקן Supabase `sbp_37f7…`** — הודבק בצ'אט טלגרם ב-2026-08-01, כלומר נמצא בשרתי טלגרם ובלוגים. `supabase login` מחדש לוקח שניות.
- [ ] **להחליט על Threaded Mode** — כרגע כבוי. דלוק = שרשורים נייטיביים אבל **אפס reply keyboards** (בקשת מיקום/איש קשר/יצירת בוט). כבוי = הכל עובד כרגיל.
- [ ] **להחליט אם להפעיל `understudy-drain.timer`** — כבוי בכוונה; כל טיק מפעיל את Claude. להפעיל אחרי שתעבור על הריצה לדוגמה.

### עבודה פתוחה
- [ ] **Mini App לניהול אלפרד** — לאחד את Secretbox, מרכז הקישורים, ה-TODOs האלה, והגדרות/פיצ'רים של הסוכנים לתוך Telegram Mini App אחד תחת אלפרד או AlfredOps. היום כל אחד מהם הוא דף או קובץ נפרד. ה-Mini App אמור להיות מקום אחד לנהל ממנו את המערכת מהפלאפון, כולל רשימת המשימות הזו עצמה.
- [ ] **AskUserQuestion לא מגיע למשתמש** — כפתורי השאלות נבלעים גם אחרי שכיבינו Threaded Mode, שהיה החשד הראשי. inline keyboards ידניים כן עובדים. סיבת השורש עדיין לא ידועה; בינתיים העקיפה היא רשימות ממוספרות בטקסט.
- [ ] **לקמט ולדחוף את תיקון מדידת העלות ב-understudy** — `orchestrator.sh` (billing_mode/parse_usage/--costs), `CLAUDE.md`, `.gitignore`. שיפור אמיתי שיילך לאיבוד בעדכון הבא של הריפו.
- [ ] **להחליט מה עושים עם ההמרה של x-reader** — 28 קבצים הומרו מנתיבי `D:/projects` ל-env (`X_READER_ROOT`, `BRAIN_ROOT`), עם fallback שלא שובר Windows. לא מקומיט.
- [ ] **לקמט את שינויי alfred** — `tgbridge/config.py` (`BRIDGE_STATE_DIR`, `BRIDGE_ENV_FILE`), `.gitignore`, `daily_backup.py`, `opsnotify.sh`, `status_page.py`. שים לב: `main.py`, `outbox.py` ושני קבצי בדיקה היו לא-מקומיטים מלפני הסשן הזה — לא שלי.
- [ ] **second-brain לא קולט חומר חדש** — הקליטה האחרונה 2026-07-23. תוצאה ישירה של סשן ה-X החסר (ראה למעלה); ההטמעה עצמה כן רצה יומית.

### הערות
- דפי here.now: סקירת ריפואים `sandy-savoy-7xd6` · מצב התיבה `mighty-zinnia-3yp6`. שניהם מוגני סיסמה, המפתח ב-`~/.herenow/credentials`.
- Ollama **לא** נדרש יותר — הטמעת second-brain מנותבת דרך Claude ב-understudy במקום Gemma מקומי. סגור בעיצוב.
- `alfred` הוא ריפו **ציבורי**. `.env-*`, `state-*/`, `.token-*` ו-`.herenow/` נוספו ל-.gitignore ב-2026-08-01 — הם לא נתפסו ע"י `.env.*` הקיים.

---

## 🔴 High priority

*(nothing open — see Done 2026-07-31)*

---

## 🟡 Medium priority (from PLAN-stability-ux.md 2026-07-03)

### [ ] x-reader: restore the signed-in X session on the Oracle box

Reading X links already works without any session — `x-reader/fetch.mjs` goes
through X's public syndication endpoint (no login, no browser, expands t.co).
What is still missing is **feed capture**: `scrape.mjs` needs a signed-in
profile at `x-reader/profile`, and `login.mjs` opens a visible Chrome window,
which a headless box has no way to show. Until this is done, second-brain's
`raw/` does not grow on this machine — 215 of its 276 captures came from the
X feed, and the newest capture is 2026-07-23.

Three ways to fix, cheapest first:
1. Copy the `profile/` directory over from the Windows box (if still reachable).
2. Inject the `auth_token` + `ct0` cookies exported from a signed-in browser
   into a fresh persistent profile.
3. Run Xvfb + x11vnc on the box, expose it over Tailscale, and sign in once
   from the phone.

Deferred deliberately on 2026-08-01 — the critical read path is covered.

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
- [x] **Dedicated Second Brain Managed Bot** — one-tap Telegram creation,
      isolated bridge/state, X Reader capture, brain search/read/ingestion and
      here.now publishing tools — 2026-08-03
- [x] **Automatic engine failover on exhausted usage** — a rejected Claude turn
      is replayed through Codex and the live session switches to Codex; Codex
      quota exhaustion replays through Claude and switches back. Narrow matching
      avoids treating network/context-limit failures as plan exhaustion — 2026-08-02
- [x] **Boot-time DNS crash loop** — the 2026-08-01 04:47 boot came up before the
      resolver worked; PTB's get_me() raised, the bridge exited rc=1 in ~1s, and
      the supervisor ladder parked it on the 300s rung — 47 min offline, 13 fast
      exits. main._await_dns() now waits for api.telegram.org (2s→30s backoff,
      15 min budget) before run_polling. Verified by breaking DNS on a live box:
      recovers in 6s with zero process restarts — 2026-08-01
- [x] **DNS failover** — DHCP handed out only Oracle's VCN resolver
      (169.254.169.254), so one sick resolver took the whole box offline.
      /etc/netplan/99-alfred-dns.yaml adds 1.1.1.1 + 9.9.9.9 after it (VCN name
      resolution unchanged); alfred.service now orders After=nss-lookup.target
      systemd-resolved.service — 2026-08-01
- [x] **Outbox shutdown spin** — a closed event loop made outbox._run log-and-
      continue on every iteration: 873 identical tracebacks in <1s on one Ctrl-C.
      A dead loop now stops the sender; any other repeating error backs off
      instead of hot-looping — 2026-08-01
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
