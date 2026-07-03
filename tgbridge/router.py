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


@dataclass
class RouterConfig:
    enabled: bool = True
    mode: str = "free_only"                    # off | free_only | full
    tag_replies: bool = True
    per_agent: dict = field(default_factory=dict)   # {name: {enabled, mode}}
    providers: list = field(default_factory=lambda: [dict(p) for p in DEFAULT_PROVIDERS])
    classifier: dict = field(default_factory=lambda: dict(DEFAULT_CLASSIFIER))

    def to_dict(self) -> dict:
        return {"enabled": self.enabled, "mode": self.mode,
                "tag_replies": self.tag_replies, "per_agent": self.per_agent,
                "providers": self.providers, "classifier": self.classifier}

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
