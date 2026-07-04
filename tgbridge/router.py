"""Pre-prompt model router + free-model backends.

Before every USER prompt the router decides who should answer:

  * FREE path — trivial, self-contained text asks (quick Q&A, translation,
    define, summarize pasted text, small talk) get answered by a FREE model
    (local Ollama or a free cloud tier), bypassing the Claude session. Saves
    Anthropic tokens and answers instantly even while Claude is busy.
  * CLAUDE path — everything else goes to the Claude session exactly as today;
    in `full` mode the router may also pick the Anthropic tier per prompt for
    agents whose model is "" (auto).

HARD RULE (fail-safe): the router must NEVER lose or block a message. Every
public entry point is wrapped so any exception → log.warning → the caller
falls through to the normal Claude session. The free path is text-only by
design; it never sees file contents or tool output — only the user's message
plus a tiny rolling history.

State lives under state/ and is written through config.save_json (the single
writer lock). Env kill switch: BRIDGE_ROUTER=0 disables everything.
"""

from __future__ import annotations

import json
import logging
import os
import re
import time
from collections import deque
from dataclasses import dataclass, field
from datetime import date

from . import config
from .config import STATE_DIR, load_json, save_json

log = logging.getLogger("bridge.router")

ROUTER_FILE = STATE_DIR / "router.json"
USAGE_FILE = STATE_DIR / "router-usage.json"
LOG_FILE = STATE_DIR / "router-log.jsonl"

# Env kill switch — evaluated live so a restart isn't needed to flip it.
def _env_disabled() -> bool:
    return os.environ.get("BRIDGE_ROUTER", "1").strip().lower() in ("0", "false", "off", "no")


# --------------------------------------------------------------------------- #
# Config
# --------------------------------------------------------------------------- #
# Default answer chain (ranked by free Hebrew quality / limits — see PLAN):
#   Gemini 3 Flash → OpenRouter gemma-4-31b-it:free → Groq gpt-oss-120b →
#   local Ollama qwen2.5:14b (unlimited last resort before Claude).
DEFAULT_PROVIDERS = [
    {"name": "gemini", "base_url": "https://generativelanguage.googleapis.com/v1beta/openai",
     "model": "gemini-flash-latest", "env_key": "GEMINI_API_KEY",
     "rpm": 10, "rpd": 200, "max_chars": 6000},
    {"name": "openrouter", "base_url": "https://openrouter.ai/api/v1",
     "model": "google/gemma-4-31b-it:free", "env_key": "OPENROUTER_API_KEY",
     "rpm": 20, "rpd": 40, "max_chars": 6000},
    {"name": "groq", "base_url": "https://api.groq.com/openai/v1",
     "model": "openai/gpt-oss-120b", "env_key": "GROQ_API_KEY",
     "rpm": 30, "rpd": 500, "max_chars": 4000},
    {"name": "ollama", "base_url": "http://127.0.0.1:11434/v1",
     "model": "qwen2.5:14b", "env_key": "", "rpm": 0, "rpd": 0, "max_chars": 6000},
]

DEFAULT_CLASSIFIER = {
    "ollama_model": "gemma4:e4b",
    "groq_model": "llama-3.1-8b-instant",
    "timeout_s": 4,
}

# Prompt-refinement stage (see PLAN-router-refine.md). mode ∈ off|auto|always.
#   auto  = rewrite only task-shaped claude prompts (default)
#   always= rewrite every eligible claude prompt (skips the task-shaped gate)
#   off   = never rewrite
DEFAULT_REFINE = {
    "enabled": True,
    "mode": "auto",
    "model": "gemini",
    "min_chars": 40,
    "show": True,
}


@dataclass
class RouterConfig:
    enabled: bool = True
    mode: str = "free_only"                    # off | free_only | full
    tag_replies: bool = True
    per_agent: dict = field(default_factory=dict)   # {name: {enabled, mode}}
    providers: list = field(default_factory=lambda: [dict(p) for p in DEFAULT_PROVIDERS])
    classifier: dict = field(default_factory=lambda: dict(DEFAULT_CLASSIFIER))
    refine: dict = field(default_factory=lambda: dict(DEFAULT_REFINE))

    def to_dict(self) -> dict:
        return {"enabled": self.enabled, "mode": self.mode,
                "tag_replies": self.tag_replies, "per_agent": self.per_agent,
                "providers": self.providers, "classifier": self.classifier,
                "refine": self.refine}

    @classmethod
    def from_dict(cls, d: dict) -> "RouterConfig":
        d = d or {}
        return cls(
            enabled=bool(d.get("enabled", True)),
            mode=d.get("mode", "free_only"),
            tag_replies=bool(d.get("tag_replies", True)),
            per_agent=dict(d.get("per_agent", {})),
            providers=list(d.get("providers") or [dict(p) for p in DEFAULT_PROVIDERS]),
            classifier={**DEFAULT_CLASSIFIER, **(d.get("classifier") or {})},
            refine={**DEFAULT_REFINE, **(d.get("refine") or {})},
        )

    def agent_mode(self, agent: str) -> str:
        """Effective mode for one agent (per-agent override wins). off if the
        agent (or the router) is disabled."""
        if not self.enabled:
            return "off"
        ov = self.per_agent.get(agent) or {}
        if "enabled" in ov and not ov.get("enabled"):
            return "off"
        return ov.get("mode", self.mode)


_CFG: RouterConfig | None = None


def load_config(force: bool = False) -> RouterConfig:
    """Load (and lazily create-with-defaults) the router config."""
    global _CFG
    if _CFG is not None and not force:
        return _CFG
    if ROUTER_FILE.exists():
        _CFG = RouterConfig.from_dict(load_json(ROUTER_FILE, {}))
    else:
        _CFG = RouterConfig()
        save_config(_CFG)
    return _CFG


def save_config(cfg: RouterConfig | None = None) -> None:
    global _CFG
    if cfg is not None:
        _CFG = cfg
    if _CFG is not None:
        save_json(ROUTER_FILE, _CFG.to_dict())


# --------------------------------------------------------------------------- #
# Decision
# --------------------------------------------------------------------------- #
@dataclass
class Decision:
    route: str = "claude"        # free | claude
    tier: str = ""               # "" | haiku | claude-sonnet-5 | opus
    task: str = "other"          # chat | translate | summarize | other
    reason: str = ""
    source: str = "heuristic"    # heuristic | llm | forced | failsafe
    text: str = ""               # possibly prefix-stripped text to actually use
    refine_skip: bool = False    # !raw override: skip prompt-refinement for this turn


# tier words the LLM classifier may return -> Anthropic model ids
_TIER_MAP = {"light": "haiku", "medium": "claude-sonnet-5", "heavy": "opus",
             "haiku": "haiku", "sonnet": "claude-sonnet-5",
             "claude-sonnet-5": "claude-sonnet-5", "opus": "opus"}

# Forced tier prefixes -> Anthropic model id (claude path).
_FORCE_TIER = {"!opus": "opus", "!sonnet": "claude-sonnet-5",
               "!haiku": "haiku", "!fable": "claude-fable-5"}

# Action / repo signal words that always mean "this needs the real agent".
_ACTION_EN = re.compile(
    r"\b(build|fix|run|commit|push|deploy|install|refactor|test|debug|"
    r"clone|merge|rebase|edit|write file|create file|create a file|"
    r"implement|compile|launch|restart|schedule|remind)\b", re.I)
_ACTION_HE = ("תבנה", "תתקן", "תריץ", "תבדוק", "תדחוף", "תפרוס", "תתקין",
              "צור", "תוסיף", "תשנה", "קומיט", "תמחק", "תכתוב", "תריצי",
              "תעשה", "הרץ", "בנה")
_DRIVE_PATH = re.compile(r"[a-zA-Z]:[\\/]")     # C:\ , D:/ …
_SUMMARIZE_VERB = re.compile(
    r"\b(summari[sz]e|tl;?dr|תסכם|סכם|תמצת|קצר)\b", re.I)


def _project_tokens(session) -> set[str]:
    """Lower-cased project/basename tokens from agents.json workdirs — a
    message mentioning one is almost certainly about that project → claude."""
    toks: set[str] = set()
    try:
        agents = getattr(session.mgr, "agents", {}) or {}
        for cfg in agents.values():
            wd = (getattr(cfg, "workdir", "") or "").rstrip("\\/")
            base = re.split(r"[\\/]", wd)[-1] if wd else ""
            if len(base) >= 4:
                toks.add(base.lower())
    except Exception as e:
        log.debug("project-token scan failed: %s", e)
    return toks


def _heuristic(text: str, session) -> Decision | None:
    """Fast, no-LLM classification. Returns a firm Decision, or None meaning
    'undecided — escalate to the LLM classifier'."""
    raw = text or ""
    low = raw.lstrip()

    # 1. Forced prefixes (strip the prefix from the text).
    first = low.split(None, 1)
    head = first[0].lower() if first else ""
    rest = first[1] if len(first) > 1 else ""
    if head in ("!c", "!claude"):
        return Decision("claude", "", "other", "forced claude", "forced", rest)
    if head in ("!f", "!free"):
        return Decision("free", "", "chat", "forced free", "forced", rest)
    if head in ("!raw", "!r"):
        # claude route, prefix stripped, but refinement explicitly skipped for
        # this one turn. source="heuristic" (not "forced") so it behaves like a
        # normal claude turn otherwise; refine_skip is what suppresses rewrite.
        return Decision("claude", "", "other", "forced raw (no refine)",
                        "heuristic", rest, refine_skip=True)
    if head in _FORCE_TIER:
        return Decision("claude", _FORCE_TIER[head], "other",
                        f"forced {head}", "forced", rest)

    # 3. Slash commands / bracketed bridge context (file refs, replies).
    if low.startswith("/"):
        return Decision("claude", "", "other", "slash command", "heuristic", raw)
    if "[received file" in raw or "[replying to:" in raw:
        return Decision("claude", "", "other", "bracket context", "heuristic", raw)

    # 4. Mid-dialog: pending questions, or Claude's last turn ended with "?".
    try:
        for st in (getattr(session, "questions", {}) or {}).values():
            f = st.get("future")
            if f is not None and not f.done():
                return Decision("claude", "", "other", "pending question",
                                "heuristic", raw)
    except Exception as e:
        log.debug("pending-question check failed: %s", e)
    try:
        hist = getattr(session, "free_history", None)
        if hist:
            last_a = hist[-1][1] if hist[-1] and len(hist[-1]) > 1 else ""
            if last_a.rstrip().endswith("?"):
                return Decision("claude", "", "other", "mid-dialog (assistant asked)",
                                "heuristic", raw)
    except Exception as e:
        log.debug("mid-dialog check failed: %s", e)

    # 5. Action verbs / repo signals / code fences / drive paths.
    if "```" in raw or _DRIVE_PATH.search(raw):
        return Decision("claude", "", "other", "code/path signal", "heuristic", raw)
    if _ACTION_EN.search(raw) or any(w in raw for w in _ACTION_HE):
        return Decision("claude", "", "other", "action verb", "heuristic", raw)
    for tok in _project_tokens(session):
        if tok in raw.lower():
            return Decision("claude", "", "other", f"project token '{tok}'",
                            "heuristic", raw)

    # 6. Very long input that is NOT a summarize request → claude.
    if len(raw) > 2000 and not _SUMMARIZE_VERB.search(raw):
        return Decision("claude", "", "other", "very long non-summary",
                        "heuristic", raw)

    return None   # undecided — let the LLM classifier weigh in


def _history_msgs(session, limit: int) -> list[dict]:
    """Last `limit` (user, assistant) exchanges as OpenAI-style messages."""
    out: list[dict] = []
    try:
        hist = list(getattr(session, "free_history", []) or [])[-limit:]
        for pair in hist:
            if not pair:
                continue
            u = pair[0] if len(pair) > 0 else ""
            a = pair[1] if len(pair) > 1 else ""
            if u:
                out.append({"role": "user", "content": str(u)[:800]})
            if a:
                out.append({"role": "assistant", "content": str(a)[:800]})
    except Exception as e:
        log.debug("history build failed: %s", e)
    return out


_CLASSIFY_SYS = (
    "You are a fast router for a personal Telegram assistant. Classify the "
    "user's LAST message. If the request needs tools, files, code, memory of "
    "the ongoing project, or actions on the user's machine → claude. Only "
    "pure-text self-contained asks (quick Q&A, definitions, translation, "
    "summarizing pasted text, small talk) → free. When unsure → claude. "
    'Reply with ONLY compact JSON: {"route":"free|claude","task":"chat|'
    'translate|summarize|other","tier":"light|medium|heavy"}.'
)


def _parse_classify_json(raw: str) -> dict | None:
    if not raw:
        return None
    m = re.search(r"\{.*\}", raw, re.S)
    if not m:
        return None
    try:
        return json.loads(m.group(0))
    except Exception:
        return None


async def _llm_classify(text: str, session, cfg: RouterConfig) -> Decision:
    """One strict-JSON classification call: Ollama gemma first, Groq 8b-instant
    fallback, pure-heuristic (claude) failsafe. Never raises."""
    clf = cfg.classifier
    timeout = float(clf.get("timeout_s", 4))
    msgs = [{"role": "system", "content": _CLASSIFY_SYS}]
    msgs += _history_msgs(session, 2)
    msgs.append({"role": "user", "content": text[:1500]})

    attempts = []
    om = clf.get("ollama_model")
    if om:
        attempts.append(("http://127.0.0.1:11434/v1", "", om))
    gk = os.environ.get("GROQ_API_KEY", "")
    gm = clf.get("groq_model")
    if gk and gm:
        attempts.append(("https://api.groq.com/openai/v1", gk, gm))

    for base_url, key, model in attempts:
        try:
            data = await _chat(base_url, key, model, msgs, timeout=timeout,
                               max_tokens=60, temperature=0.0)
            parsed = _parse_classify_json(data or "")
            if parsed and parsed.get("route") in ("free", "claude"):
                route = parsed["route"]
                task = parsed.get("task", "other")
                if task not in ("chat", "translate", "summarize", "other"):
                    task = "other"
                tier = _TIER_MAP.get(str(parsed.get("tier", "")).lower(), "")
                return Decision(route, tier, task, "llm classify", "llm", text)
        except Exception as e:   # network/parse error — logged, try next attempt
            log.debug("classify via %s failed: %s", base_url, e)
    return Decision("claude", "", "other", "classifier unavailable", "failsafe", text)


async def classify(text: str, session) -> Decision:
    """Decide free vs claude for one USER message. Never raises — any internal
    error yields a claude Decision (fail-safe = current behavior)."""
    try:
        cfg = load_config()
        agent = getattr(getattr(session, "cfg", None), "name", "main")
        if _env_disabled() or cfg.agent_mode(agent) == "off":
            return Decision("claude", "", "other", "router off", "forced", text)
        h = _heuristic(text, session)
        if h is not None:
            # forced-free requested but... still honour it (user asked).
            return h
        d = await _llm_classify(text, session, cfg)
        return d
    except Exception as e:
        log.warning("classify failed, falling through to Claude: %s", e)
        return Decision("claude", "", "other", f"classify error: {e}", "failsafe", text)


# --------------------------------------------------------------------------- #
# Usage accounting (per-day per-provider counts) + in-memory RPM throttle
# --------------------------------------------------------------------------- #
_rpm_hits: dict[str, deque] = {}


def _load_usage() -> dict:
    u = load_json(USAGE_FILE, {})
    today = date.today().isoformat()
    if u.get("date") != today:
        u = {"date": today, "counts": {}}
    u.setdefault("counts", {})
    return u


def usage_today() -> dict:
    """{provider: count} for today (public, for /router)."""
    return dict(_load_usage().get("counts", {}))


def _bump_usage(provider: str) -> None:
    try:
        u = _load_usage()
        u["counts"][provider] = int(u["counts"].get(provider, 0)) + 1
        save_json(USAGE_FILE, u)
    except Exception as e:
        log.debug("usage bump failed: %s", e)


def _rpd_spent(provider: dict, counts: dict) -> bool:
    rpd = int(provider.get("rpd", 0) or 0)
    if rpd <= 0:
        return False
    return int(counts.get(provider["name"], 0)) >= rpd


def _rpm_throttled(provider: dict) -> bool:
    rpm = int(provider.get("rpm", 0) or 0)
    if rpm <= 0:
        return False
    name = provider["name"]
    dq = _rpm_hits.setdefault(name, deque())
    now = time.monotonic()
    while dq and now - dq[0] > 60.0:
        dq.popleft()
    return len(dq) >= rpm


def _rpm_record(provider: dict) -> None:
    if int(provider.get("rpm", 0) or 0) > 0:
        _rpm_hits.setdefault(provider["name"], deque()).append(time.monotonic())


# --------------------------------------------------------------------------- #
# Free-model answering
# --------------------------------------------------------------------------- #
_ANSWER_SYS = (
    "You are the quick-reply sidekick of a Telegram assistant. Answer briefly "
    "(<=1500 chars) in the user's language (usually Hebrew). If the task "
    "actually needs tools, files, code, or the main assistant's project "
    "memory, reply with exactly ROUTE_TO_CLAUDE."
)

SHORT_NAME = {"gemini": "gemini", "openrouter": "gemma", "groq": "groq",
              "ollama": "local", "gemma-google": "gemma", "nvidia": "nvidia",
              "cerebras": "cerebras"}

# Some open models (Gemma 4, R1-style) leak chain-of-thought into content as
# <thought>/<think> blocks — strip them; an unclosed tag (reasoning ate the
# token budget) drops the tail so the caller can skip to the next provider.
_REASONING_RE = re.compile(r"<(thought|think|thinking)>.*?</\1>\s*",
                           re.DOTALL | re.IGNORECASE)


def _strip_reasoning(ans: str) -> str:
    out = _REASONING_RE.sub("", ans)
    m = re.search(r"<(thought|think|thinking)>", out, re.IGNORECASE)
    if m:
        out = out[:m.start()]
    return out


async def _chat(base_url: str, key: str, model: str, messages: list[dict],
                timeout: float = 20.0, max_tokens: int = 900,
                temperature: float = 0.3) -> str | None:
    """One OpenAI-compatible /chat/completions call. Returns the content string
    or None. Raises httpx errors up to the caller (which handles them)."""
    import httpx
    headers = {"Content-Type": "application/json"}
    if key:
        headers["Authorization"] = f"Bearer {key}"
    payload = {"model": model, "messages": messages,
               "max_tokens": max_tokens, "temperature": temperature}
    url = base_url.rstrip("/") + "/chat/completions"
    async with httpx.AsyncClient(timeout=timeout) as client:
        r = await client.post(url, headers=headers, json=payload)
        r.raise_for_status()
        data = r.json()
    try:
        return data["choices"][0]["message"]["content"]
    except (KeyError, IndexError, TypeError):
        return None


async def answer_free(text: str, session, decision: Decision) -> tuple[str, str] | None:
    """Walk the provider chain and return (answer, provider_short) or None.
    None → caller falls through to Claude. Never raises. Skips providers whose
    env key is missing, whose per-day budget is spent, or that are RPM-throttled.
    Total wall-clock budget ~45 s."""
    try:
        cfg = load_config()
        counts = _load_usage().get("counts", {})
        messages = [{"role": "system", "content": _ANSWER_SYS}]
        messages += _history_msgs(session, 8)
        messages.append({"role": "user", "content": text})
        deadline = time.monotonic() + 45.0

        for prov in cfg.providers:
            if time.monotonic() >= deadline:
                break
            name = prov.get("name", "?")
            env_key = prov.get("env_key", "")
            key = os.environ.get(env_key, "") if env_key else ""
            if env_key and not key:
                continue                          # provider disabled (no key)
            if _rpd_spent(prov, counts):
                continue
            if _rpm_throttled(prov):
                continue
            max_chars = int(prov.get("max_chars", 6000) or 6000)
            if len(text) > max_chars:
                continue                          # too big for this provider
            t0 = time.monotonic()
            ok = False
            ans = None
            try:
                _rpm_record(prov)
                ans = await _chat(prov["base_url"], key, prov["model"],
                                  messages, timeout=20.0)
                ok = bool(ans)
            except Exception as e:   # network/parse error — logged, try next provider
                log.debug("answer via %s failed: %s", name, e)
            latency_ms = int((time.monotonic() - t0) * 1000)
            if ok and ans:
                cleaned = _strip_reasoning(ans).strip()
                short = SHORT_NAME.get(name, name)
                if cleaned == "ROUTE_TO_CLAUDE":
                    _log_decision(session, text, decision, provider=short,
                                  latency_ms=latency_ms, ok=False,
                                  reason="model deferred to claude")
                    return None                    # model itself punted
                if not cleaned:                    # only reasoning, no answer
                    log.debug("answer via %s empty after reasoning strip", name)
                    continue
                _bump_usage(name)
                _log_decision(session, text, decision, provider=short,
                              latency_ms=latency_ms, ok=True,
                              reason=decision.reason)
                return cleaned[:1500], short
            # failure → try next provider
        _log_decision(session, text, decision, provider="", latency_ms=0,
                      ok=False, reason="all providers exhausted")
        return None
    except Exception as e:
        log.warning("answer_free failed, falling through to Claude: %s", e)
        return None


# --------------------------------------------------------------------------- #
# Prompt-refinement stage (grounded in the Second-Brain loop/prompt guides)
# --------------------------------------------------------------------------- #
# The rewrite is grounded in these wiki pages (read live if present). If they
# are missing/unreadable we fall back to the embedded distilled rubric below.
_REFINE_WIKI = [
    r"D:\Projects\second-brain\wiki\Loop Engineering.md",
    r"D:\Projects\second-brain\wiki\Agentic Engineering Concepts.md",
    r"D:\Projects\second-brain\wiki\AI Agents.md",
]

# Faithful distillation of the three wiki pages — used verbatim when the files
# can't be read. Never let refinement depend on disk availability.
_REFINE_RUBRIC_FALLBACK = (
    "Principles for a well-structured agent prompt (loop/prompt engineering):\n"
    "- Goal + definition of done: state the objective and a concrete, checkable "
    "success criterion. 'Task complete is a claim, not proof.'\n"
    "- Context, tight: name the relevant files/paths/constraints the agent needs; "
    "context is a budget, not a bucket — don't bloat it.\n"
    "- Self-verification: ask the agent to check its own work with a real check "
    "(run tests / a command / observe output) before claiming done.\n"
    "- Scope + stop conditions: for anything repetitive, bound it (max scope, what "
    "'enough' means).\n"
    "- Plan-before, log-after for multi-step work.\n"
    "- Preserve intent & language. RESTRUCTURE, never add scope the user didn't "
    "ask for, never invent requirements. Keep it concise."
)

# Cache the assembled rubric keyed by the max mtime of the source files; re-read
# only when a file changes.
_refine_rubric_cache: tuple[float, str] | None = None


def _load_refine_rubric() -> str:
    """Concatenate the '## Key points' section of each wiki page (or the whole
    file if that heading is absent), trimmed to ~3500 chars. Cached by max mtime.
    Falls back to the embedded rubric if the files are missing/unreadable. Never
    raises."""
    global _refine_rubric_cache
    try:
        import os as _os
        mtimes = []
        for p in _REFINE_WIKI:
            try:
                mtimes.append(_os.path.getmtime(p))
            except OSError:
                pass
        if not mtimes:
            return _REFINE_RUBRIC_FALLBACK
        key = max(mtimes)
        if _refine_rubric_cache is not None and _refine_rubric_cache[0] == key:
            return _refine_rubric_cache[1]

        chunks = []
        for p in _REFINE_WIKI:
            try:
                raw = open(p, encoding="utf-8").read()
            except OSError:
                continue
            # extract the "## Key points" section (up to the next "## ")
            m = re.search(r"^##\s+Key points.*?$", raw, re.M | re.I)
            if m:
                start = m.end()
                nxt = re.search(r"^##\s", raw[start:], re.M)
                body = raw[start:start + nxt.start()] if nxt else raw[start:]
            else:
                body = raw
            body = body.strip()
            if body:
                chunks.append(body)
        text = "\n\n".join(chunks).strip()
        if not text:
            return _REFINE_RUBRIC_FALLBACK
        text = text[:3500]
        _refine_rubric_cache = (key, text)
        return text
    except Exception as e:
        log.debug("refine rubric load failed, using fallback: %s", e)
        return _REFINE_RUBRIC_FALLBACK


def should_refine(text: str, decision: Decision, cfg: RouterConfig) -> bool:
    """True only for user→claude, task-shaped prompts eligible for a rewrite.
    Never raises (any error → False = pass through untouched)."""
    try:
        rf = cfg.refine or {}
        if not rf.get("enabled", True):
            return False
        mode = rf.get("mode", "auto")
        if mode == "off":
            return False
        if decision.route != "claude":
            return False
        if decision.refine_skip:
            return False              # !raw override
        if decision.source == "forced":
            return False              # !c / !opus / router-off etc.
        raw = text or ""
        if raw.lstrip().startswith("/"):
            return False              # slash command
        if "[received file" in raw or "[replying to:" in raw:
            return False              # bridge-injected context
        # An explicit task signal (action verb / drive path / code fence) means
        # "this is real work" regardless of length — refine it. The min_chars
        # floor only guards the borderline cases (short chat that lacks a signal).
        explicit_task = bool(
            _ACTION_EN.search(raw)
            or any(w in raw for w in _ACTION_HE)
            or _DRIVE_PATH.search(raw)
            or "```" in raw
        )
        min_chars = int(rf.get("min_chars", 40))
        if mode == "always":
            # rewrite everything eligible, but still ignore trivially short input
            return explicit_task or len(raw) >= min_chars
        # mode == "auto": task-shaped = an explicit signal OR a long (>300) prompt.
        return explicit_task or len(raw) > 300
    except Exception as e:
        log.debug("should_refine failed, skipping refine: %s", e)
        return False


_REFINE_SYS_TMPL = (
    "{rubric}\n\n"
    "Rewrite the user's request into a clearer, better-structured prompt for a "
    "capable coding agent, following the principles above. PRESERVE the original "
    "intent and language exactly (usually Hebrew). Do NOT add requirements, scope, "
    "or facts the user did not state — only restructure, clarify, and make the "
    "success criterion explicit. Be concise. Output ONLY the rewritten prompt text "
    "— no preamble, no explanation, no markdown fences, no 'Here is...'."
)

# Refusal / meta prefixes that mean the model editorialized instead of rewriting.
_REFINE_META_PREFIXES = ("here is", "here's", "הנה", "```", "sure", "certainly",
                         "rewritten", "refined prompt", "prompt:", "בבקשה")


def _refine_provider_chain(cfg: RouterConfig) -> list[dict]:
    """Ordered provider dicts to try for refinement: the configured model first
    (default 'gemini'), then the rest of the answer chain, de-duplicated."""
    want = (cfg.refine or {}).get("model", "gemini")
    provs = list(cfg.providers or [])
    ordered = [p for p in provs if p.get("name") == want]
    ordered += [p for p in provs if p.get("name") != want]
    return ordered


async def refine(text: str, session, cfg: RouterConfig):
    """Rewrite `text` into a better-structured prompt via a free model.
    Returns (refined_text, True) on a good rewrite, or None to use the original.
    NEVER raises — every failure path returns None (fail-safe = original text)."""
    try:
        original = text or ""
        rubric = _load_refine_rubric()
        sys_prompt = _REFINE_SYS_TMPL.format(rubric=rubric)
        messages = [{"role": "system", "content": sys_prompt},
                    {"role": "user", "content": original[:6000]}]
        counts = _load_usage().get("counts", {})
        deadline = time.monotonic() + 30.0

        for prov in _refine_provider_chain(cfg):
            if time.monotonic() >= deadline:
                break
            name = prov.get("name", "?")
            env_key = prov.get("env_key", "")
            key = os.environ.get(env_key, "") if env_key else ""
            if env_key and not key:
                continue                          # provider disabled (no key)
            if _rpd_spent(prov, counts):
                continue
            if _rpm_throttled(prov):
                continue
            ans = None
            try:
                _rpm_record(prov)
                ans = await _chat(prov["base_url"], key, prov["model"], messages,
                                  timeout=20.0, max_tokens=700, temperature=0.3)
            except Exception as e:   # network/parse error — try next provider
                log.debug("refine via %s failed: %s", name, e)
                continue
            if not ans:
                continue
            refined = _strip_reasoning(ans).strip()
            # --- degeneracy guards → use original (return None) ---
            if not refined:
                continue                          # empty → try next provider
            low = refined.lower()
            if refined.strip() == original.strip():
                return None                       # no change
            if any(low.startswith(p) for p in _REFINE_META_PREFIXES):
                return None                       # refusal / meta / fenced (```)
            # Ballooning guard = added scope. A terse one-liner LEGITIMATELY
            # expands several-fold once its implicit goal + success-criterion are
            # made explicit (that IS the feature), so the 4x ratio only signals
            # scope-creep once the original is already substantial (>=200 chars).
            # Short prompts are bounded by a generous absolute cap (1200 chars —
            # enough for a well-structured goal/steps/success-criterion rewrite,
            # small enough to catch a runaway dump), plus a hard 3000 ceiling and
            # the model's own max_tokens (~700) for everything.
            too_long = (
                len(refined) > 3000
                or (len(original) >= 200 and len(refined) > 4 * len(original))
                or (len(original) < 200 and len(refined) > 1200)
            )
            if too_long:
                return None                       # ballooned = probably added scope
            if len(original) > 200 and len(refined) < len(original) * 0.5:
                return None                       # truncated
            # --- good rewrite ---
            _bump_usage(name)
            _log_decision(session, original, decision=Decision(
                route="claude", source="refine", reason="refined"),
                provider=SHORT_NAME.get(name, name), ok=True,
                reason=f"refined via {name}")
            return refined, True
        return None
    except Exception as e:
        log.warning("refine failed, using original: %s", e)
        return None


# --------------------------------------------------------------------------- #
# Decision log (state/router-log.jsonl)
# --------------------------------------------------------------------------- #
def _log_decision(session, text: str, decision: Decision, provider: str = "",
                  latency_ms: int = 0, ok: bool = True, reason: str = "") -> None:
    try:
        rec = {
            "ts": time.strftime("%Y-%m-%dT%H:%M:%S"),
            "agent": getattr(getattr(session, "cfg", None), "name", "?"),
            "chars": len(text or ""),
            "route": decision.route,
            "task": decision.task,
            "tier": decision.tier,
            "provider": provider,
            "latency_ms": latency_ms,
            "ok": ok,
            "reason": reason or decision.reason,
            "preview": (text or "")[:60],
        }
        with LOG_FILE.open("a", encoding="utf-8") as f:
            f.write(json.dumps(rec, ensure_ascii=False) + "\n")
        _rotate_log()
    except Exception as e:
        log.debug("router log write failed: %s", e)


def log_claude(session, text: str, decision: Decision) -> None:
    """Public: record that a message went the Claude route."""
    _log_decision(session, text, decision, provider="", latency_ms=0,
                  ok=True, reason=decision.reason)


def _rotate_log() -> None:
    try:
        if LOG_FILE.exists() and LOG_FILE.stat().st_size > 2 * 1024 * 1024:
            lines = LOG_FILE.read_text(encoding="utf-8").splitlines()[-500:]
            LOG_FILE.write_text("\n".join(lines) + "\n", encoding="utf-8")
    except Exception as e:
        log.debug("router log rotate failed: %s", e)


def recent_decisions(n: int = 10) -> list[dict]:
    try:
        lines = LOG_FILE.read_text(encoding="utf-8").splitlines()[-n:]
        return [json.loads(ln) for ln in lines if ln.strip()]
    except Exception:
        return []


# --------------------------------------------------------------------------- #
# Classifier health ping (for /router)
# --------------------------------------------------------------------------- #
async def classifier_health() -> bool:
    """True if the local Ollama classifier answers a 1-token ping."""
    try:
        cfg = load_config()
        model = cfg.classifier.get("ollama_model", "gemma4:e4b")
        out = await _chat("http://127.0.0.1:11434/v1", "", model,
                          [{"role": "user", "content": "ping"}],
                          timeout=4.0, max_tokens=1, temperature=0.0)
        return out is not None
    except Exception:
        return False
