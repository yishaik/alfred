"""Read-only multi-model council using subscription CLIs plus Grok API.

The council is deliberately advisory. It never grants file-write or deployment
permissions; the primary Alfred/Claude session remains the executor and approval gate.
"""
from __future__ import annotations

import argparse
import asyncio
import json
import os
from dataclasses import asdict, dataclass
from pathlib import Path
from typing import Awaitable, Callable

import httpx

from .security import CommandResult, PolicyError, resolve_under, run_command


@dataclass
class Opinion:
    provider: str
    ok: bool
    model: str
    text: str
    error: str = ""


def _extract_jsonish(result: CommandResult) -> str:
    raw = result.stdout.strip()
    if not raw:
        return ""
    try:
        data = json.loads(raw)
    except json.JSONDecodeError:
        return raw
    if isinstance(data, dict):
        for key in ("result", "response", "output_text", "text", "content"):
            if isinstance(data.get(key), str):
                return data[key]
    return json.dumps(data, ensure_ascii=False)


async def claude(prompt: str, cwd: Path) -> Opinion:
    model = os.environ.get("ALFRED_CLAUDE_MODEL", "claude-fable-5")
    argv = [
        "claude", "-p", prompt, "--output-format", "json", "--permission-mode", "plan",
        "--max-turns", "1", "--model", model,
    ]
    try:
        result = await asyncio.to_thread(run_command, argv, cwd=cwd, timeout=900)
        return Opinion("claude", result.returncode == 0, model, _extract_jsonish(result), result.stderr)
    except Exception as exc:
        return Opinion("claude", False, model, "", str(exc))


async def codex(prompt: str, cwd: Path) -> Opinion:
    model = os.environ.get("ALFRED_CODEX_MODEL", "gpt-5.6")
    argv = [
        "codex", "exec", "--ephemeral", "--json", "--sandbox", "read-only",
        "--model", model, prompt,
    ]
    try:
        result = await asyncio.to_thread(run_command, argv, cwd=cwd, timeout=900)
        return Opinion("gpt", result.returncode == 0, model, _extract_jsonish(result), result.stderr)
    except Exception as exc:
        return Opinion("gpt", False, model, "", str(exc))


async def gemini(prompt: str, cwd: Path) -> Opinion:
    model = os.environ.get("ALFRED_GEMINI_MODEL", "gemini-3.1-pro-preview")
    argv = [
        "gemini", "-p", prompt, "--output-format", "json", "--approval-mode", "plan",
        "-m", model,
    ]
    try:
        result = await asyncio.to_thread(run_command, argv, cwd=cwd, timeout=900)
        return Opinion("gemini", result.returncode == 0, model, _extract_jsonish(result), result.stderr)
    except Exception as exc:
        return Opinion("gemini", False, model, "", str(exc))


async def grok(prompt: str, _cwd: Path) -> Opinion:
    model = os.environ.get("ALFRED_GROK_MODEL", "grok-4.5-latest")
    key = os.environ.get("XAI_API_KEY", "")
    if not key:
        return Opinion("grok", False, model, "", "XAI_API_KEY is not configured")
    payload = {
        "model": model,
        "messages": [
            {"role": "system", "content": "Act as a senior code reviewer. Be concise and cite concrete risks."},
            {"role": "user", "content": prompt},
        ],
        "reasoning_effort": "high",
        "temperature": 0.1,
    }
    try:
        async with httpx.AsyncClient(timeout=180) as client:
            response = await client.post(
                "https://api.x.ai/v1/chat/completions",
                headers={"Authorization": f"Bearer {key}"},
                json=payload,
            )
            response.raise_for_status()
            data = response.json()
        text = data["choices"][0]["message"]["content"]
        return Opinion("grok", True, model, text)
    except Exception as exc:
        return Opinion("grok", False, model, "", str(exc))


PROVIDERS: dict[str, Callable[[str, Path], Awaitable[Opinion]]] = {
    "claude": claude,
    "gpt": codex,
    "gemini": gemini,
    "grok": grok,
}


async def run_council(prompt: str, cwd: Path, providers: list[str]) -> list[Opinion]:
    unknown = sorted(set(providers) - PROVIDERS.keys())
    if unknown:
        raise PolicyError(f"unknown providers: {', '.join(unknown)}")
    return await asyncio.gather(*(PROVIDERS[name](prompt, cwd) for name in providers))


def main() -> int:
    parser = argparse.ArgumentParser(description="Run a read-only multi-model council")
    parser.add_argument("prompt")
    parser.add_argument("--cwd", default=os.environ.get("BRIDGE_WORKDIR", "/data/workspaces"))
    parser.add_argument("--providers", default="claude,gpt,gemini,grok")
    args = parser.parse_args()

    root = Path(os.environ.get("BRIDGE_WORKDIR", "/data/workspaces"))
    cwd = resolve_under(root, args.cwd)
    providers = [x.strip() for x in args.providers.split(",") if x.strip()]
    opinions = asyncio.run(run_council(args.prompt, cwd, providers))
    print(json.dumps([asdict(item) for item in opinions], ensure_ascii=False, indent=2))
    return 0 if any(item.ok for item in opinions) else 2


if __name__ == "__main__":
    raise SystemExit(main())
