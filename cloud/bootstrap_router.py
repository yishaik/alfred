"""Create a current-model router config on first boot; preserve operator edits."""
from __future__ import annotations

import json
import os
from pathlib import Path


def _provider(name: str, base_url: str, model: str, env_key: str, *, manual: bool = False) -> dict:
    item = {
        "name": name,
        "base_url": base_url,
        "model": model,
        "env_key": env_key,
        "rpm": 10,
        "rpd": 200,
        "max_chars": 9000,
    }
    if manual:
        item["manual"] = True
    return item


def main() -> None:
    state_dir = Path(os.environ.get("ALFRED_DATA_DIR", "/data")) / "state"
    state_dir.mkdir(parents=True, exist_ok=True)
    path = state_dir / "router.json"
    if path.exists():
        return

    cfg = {
        "enabled": True,
        "mode": "free_only",
        "tag_replies": True,
        "per_agent": {},
        "providers": [
            _provider(
                "gemini-fast",
                "https://generativelanguage.googleapis.com/v1beta/openai",
                os.environ.get("ALFRED_GEMINI_FAST_MODEL", "gemini-3.5-flash"),
                "GEMINI_API_KEY",
            ),
            _provider(
                "grok",
                "https://api.x.ai/v1",
                os.environ.get("ALFRED_GROK_MODEL", "grok-4.5-latest"),
                "XAI_API_KEY",
                manual=True,
            ),
            _provider(
                "gpt",
                "https://api.openai.com/v1",
                os.environ.get("ALFRED_CODEX_MODEL", "gpt-5.6"),
                "OPENAI_API_KEY",
                manual=True,
            ),
        ],
        "classifier": {
            "ollama_model": "",
            "groq_model": "llama-3.1-8b-instant",
            "timeout_s": 5,
        },
        "refine": {
            "enabled": True,
            "mode": "auto",
            "model": "gemini-fast",
            "min_chars": 40,
            "show": True,
        },
    }
    tmp = path.with_suffix(".tmp")
    tmp.write_text(json.dumps(cfg, ensure_ascii=False, indent=2), encoding="utf-8")
    tmp.replace(path)


if __name__ == "__main__":
    main()
