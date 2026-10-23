"""Jev-decided multi-model router for the Telegram bridge.

Jev (TypeSafe System One) classifies each message into one class -> a concrete model
on the right provider. Returns the answer + the model label so the caller can footer it.

Providers:
  cliproxy  -> your Claude/GPT/Gemini subscriptions, via the localhost:8317 SSH tunnel to hermes
  xai       -> Grok, direct on api.x.ai

Dormant unless BRIDGE_JEV_ROUTER=1. Any failure -> caller falls through to Claude (fail-safe).
Keys are 0600 files under /home/ubuntu/alfred (never in env/transcript).
"""
from __future__ import annotations

import json
import os
import pathlib
import urllib.request
import urllib.error

_ALFRED = pathlib.Path("/home/ubuntu/alfred")
JEV_URL = "https://api.typesafe.ai/v1/systemone"

# provider -> (chat-completions url, key filename)
PROVIDERS = {
    "cliproxy": (os.environ.get("CLIPROXY_URL", "http://127.0.0.1:8317/v1/chat/completions"), ".cliproxy_key"),
    "xai":      ("https://api.x.ai/v1/chat/completions", ".xai_key"),
}

# Jev class -> (provider, model, human label)
MODMAP = {
    "coding":       ("cliproxy", "claude-sonnet-5",       "Claude Sonnet 5"),
    "reasoning":    ("cliproxy", "gpt-6-astra",           "GPT-6 Astra"),
    "creative":     ("xai",      "grok-4.6",              "Grok 4.6"),
    "fast_general": ("cliproxy", "gemini-3.8-flash-high", "Gemini 3.8 Flash"),
}
FALLBACK = ("cliproxy", "claude-sonnet-5", "Claude Sonnet 5")


def enabled() -> bool:
    return os.environ.get("BRIDGE_JEV_ROUTER", "0").strip().lower() in ("1", "true", "on", "yes")


def _key(name: str) -> str:
    try:
        return (_ALFRED / name).read_text().strip()
    except Exception:
        return ""


def _http(url: str, key: str, payload: dict, timeout: int = 90) -> dict:
    req = urllib.request.Request(url, json.dumps(payload).encode(),
                                 {"Authorization": "Bearer " + key, "Content-Type": "application/json"})
    with urllib.request.urlopen(req, timeout=timeout) as r:
        return json.loads(r.read())


def classify(text: str) -> tuple[str, float]:
    """Return (category, confidence). Raises on failure so the caller can fall through."""
    key = _key(".jev_key")
    if not key:
        raise RuntimeError("no jev key")
    d = _http(JEV_URL, key, {
        "state": (text or "")[:4000],
        "model": "jev-latest",
        "questions": {"route": {"type": "choice",
            "instructions": "Pick the single best model class to answer this user message.",
            "criteria": {
                "coding": "Programming, writing or debugging code, algorithms, technical implementation",
                "reasoning": "Hard multi-step reasoning, math, planning, careful analysis or research",
                "creative": "Creative writing, brainstorming, tone/style, humor, marketing copy",
                "fast_general": "Simple, short, factual, or casual conversation — a fast model suffices",
            }}},
    }, timeout=30)
    a = (d.get("answers") or {}).get("route") or {}
    return a.get("choice") or "fast_general", float(a.get("confidence") or 0.0)


def _complete(provider: str, model: str, msgs: list) -> str:
    url, keyfile = PROVIDERS[provider]
    key = _key(keyfile)
    if not key:
        raise RuntimeError(f"no key for {provider}")
    d = _http(url, key, {"model": model, "messages": msgs, "stream": False, "max_tokens": 1500})
    return ((d.get("choices") or [{}])[0].get("message") or {}).get("content") or ""


def _memory_block(query: str) -> str:
    """Same two memory layers the Claude session gets: pinned (always) + recall hits (for this query).
    Shared with every routed model as background context. Never raises."""
    try:
        from .memory import Memory
        from .config import KB_DIR
        mem = Memory(str(KB_DIR / "main"))
        parts = []
        pinned = (mem.render_prompt() or "").strip()
        if pinned:
            parts.append(pinned[:9000])
        hits = [h for h in mem.search(query)[:5] if h.kind != "pinned" and (h.text or "").strip()]
        if hits:
            parts.append("Relevant notes:\n- " + "\n- ".join(h.text.strip()[:700] for h in hits))
        return "\n\n".join(parts)[:12000]
    except Exception:
        return ""


def answer(text: str, history: list | None = None) -> dict:
    """Classify with Jev, answer via the chosen model, fall back to Claude on failure."""
    category, confidence = classify(text)
    provider, model, label = MODMAP.get(category, FALLBACK)
    sys_prompt = "You are a helpful, concise assistant. Answer clearly and match the user's language (Hebrew or English)."
    mem = _memory_block(text)
    if mem:
        sys_prompt += ("\n\nBACKGROUND MEMORY about this user and their work (shared across all models; "
                       "use it when relevant, never recite it unprompted):\n" + mem)
    msgs = [{"role": "system", "content": sys_prompt}]
    for m in (history or [])[-8:]:
        if isinstance(m, dict) and m.get("role") in ("user", "assistant") and isinstance(m.get("content"), str):
            msgs.append({"role": m["role"], "content": m["content"][:6000]})
    msgs.append({"role": "user", "content": (text or "")[:6000]})

    used = (provider, model, label)
    fell_back = False
    try:
        content = _complete(provider, model, msgs)
        if not content.strip():
            raise RuntimeError("empty completion")
    except Exception:
        if (provider, model) == (FALLBACK[0], FALLBACK[1]):
            raise
        provider, model, label = FALLBACK
        fell_back = True
        content = _complete(provider, model, msgs)
        if not content.strip():
            raise RuntimeError("empty completion (fallback)")
        label += " (fallback)"
    return {"answer": content, "provider": provider, "model": model, "label": label,
            "category": category, "confidence": confidence, "fell_back": fell_back}


def footer(label: str, category: str = "", confidence: float = 0.0) -> str:
    c = f" · {category} {round(confidence*100)}%" if category else ""
    return f"\n\n🤖 {label}{c}"
