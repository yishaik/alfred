"""Shared validation and subprocess safety primitives."""
from __future__ import annotations

import os
import re
import subprocess
from dataclasses import dataclass
from pathlib import Path
from typing import Iterable, Sequence

_REPO_RE = re.compile(r"^[A-Za-z0-9_.-]+/[A-Za-z0-9_.-]+$")
_REF_RE = re.compile(r"^[A-Za-z0-9._/-]{1,180}$")
_PROVIDER_RE = re.compile(r"^[a-z0-9-]{1,32}$")


class PolicyError(ValueError):
    pass


@dataclass(frozen=True)
class CommandResult:
    argv: tuple[str, ...]
    returncode: int
    stdout: str
    stderr: str


def validate_repo(repo: str) -> str:
    if not _REPO_RE.fullmatch(repo or "") or ".." in repo:
        raise PolicyError("repository must be owner/name with safe characters")
    allowed = {x.strip() for x in os.environ.get("ALFRED_ALLOWED_REPOS", "").split(",") if x.strip()}
    if allowed and repo not in allowed:
        raise PolicyError(f"repository is outside ALFRED_ALLOWED_REPOS: {repo}")
    return repo


def validate_ref(ref: str) -> str:
    if not _REF_RE.fullmatch(ref or "") or ".." in ref or ref.startswith("/"):
        raise PolicyError("unsafe git ref")
    return ref


def validate_provider(provider: str) -> str:
    if not _PROVIDER_RE.fullmatch(provider or ""):
        raise PolicyError("unsafe provider name")
    return provider


def resolve_under(root: Path, candidate: Path | str) -> Path:
    root = root.resolve()
    target = Path(candidate)
    if not target.is_absolute():
        target = root / target
    target = target.resolve()
    if target != root and root not in target.parents:
        raise PolicyError(f"path escapes workspace root: {candidate}")
    return target


def redact(text: str) -> str:
    value = text or ""
    for key, secret in os.environ.items():
        if not secret or len(secret) < 8:
            continue
        if any(token in key.upper() for token in ("TOKEN", "KEY", "SECRET", "PASSWORD")):
            value = value.replace(secret, f"<redacted:{key}>")
    return value


def run_command(
    argv: Sequence[str],
    *,
    cwd: Path | str | None = None,
    timeout: int | None = None,
    env: dict[str, str] | None = None,
    check: bool = False,
    stdin_text: str | None = None,
) -> CommandResult:
    if not argv or any("\x00" in str(arg) for arg in argv):
        raise PolicyError("invalid argv")
    max_timeout = int(os.environ.get("ALFRED_COMMAND_TIMEOUT_SECONDS", "1800"))
    effective_timeout = min(timeout or max_timeout, max_timeout)
    completed = subprocess.run(
        [str(x) for x in argv],
        cwd=str(cwd) if cwd else None,
        env=env or os.environ.copy(),
        input=stdin_text,
        capture_output=True,
        text=True,
        timeout=effective_timeout,
        shell=False,
        check=False,
    )
    limit = int(os.environ.get("ALFRED_MAX_OUTPUT_CHARS", "50000"))
    result = CommandResult(
        tuple(str(x) for x in argv),
        completed.returncode,
        redact(completed.stdout[-limit:]),
        redact(completed.stderr[-limit:]),
    )
    if check and result.returncode:
        raise subprocess.CalledProcessError(
            result.returncode, result.argv, output=result.stdout, stderr=result.stderr
        )
    return result


def production_approval(provider: str, repo: str, supplied: str | None) -> None:
    if os.environ.get("ALFRED_ALLOW_PRODUCTION_DEPLOY", "0").lower() not in {"1", "true", "yes"}:
        raise PolicyError("production deploys are disabled by ALFRED_ALLOW_PRODUCTION_DEPLOY")
    expected = f"{provider}:{repo}"
    if supplied != expected:
        raise PolicyError(f"production approval must exactly equal {expected}")


def existing_binaries(names: Iterable[str]) -> dict[str, bool]:
    import shutil

    return {name: shutil.which(name) is not None for name in names}
