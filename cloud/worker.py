"""Entrypoint for isolated Fly Machine jobs. Creates draft PRs only."""
from __future__ import annotations

import base64
import json
import os
import sys
from pathlib import Path

from .security import PolicyError, run_command, validate_ref, validate_repo


def load_job() -> dict:
    encoded = os.environ.get("ALFRED_JOB_SPEC_B64", "")
    if not encoded: raise PolicyError("ALFRED_JOB_SPEC_B64 is missing")
    data = json.loads(base64.urlsafe_b64decode(encoded.encode()))
    validate_repo(data["repo"]); validate_ref(data["ref"])
    if not isinstance(data.get("prompt"), str) or not data["prompt"].strip():
        raise PolicyError("worker prompt is empty")
    return data


def main() -> int:
    job = load_job()
    root = Path("/tmp/work"); root.mkdir(parents=True, exist_ok=True)
    repo_dir = root / job["repo"].split("/", 1)[1]
    run_command(["gh", "repo", "clone", job["repo"], str(repo_dir)], check=True)
    run_command(["git", "switch", "-c", job["ref"]], cwd=repo_dir, check=True)
    model = os.environ.get("ALFRED_CODEX_CODING_MODEL", "gpt-5.3-codex")
    prompt = job["prompt"] + "\n\nWork only in this repository. Run relevant tests. Do not deploy. Commit changes and stop."
    result = run_command(["codex", "exec", "--ephemeral", "--sandbox", "workspace-write", "--model", model, prompt], cwd=repo_dir, timeout=1800)
    if result.returncode:
        print(result.stderr, file=sys.stderr); return result.returncode
    status = run_command(["git", "status", "--porcelain"], cwd=repo_dir)
    if status.stdout.strip():
        run_command(["git", "add", "-A"], cwd=repo_dir, check=True)
        run_command(["git", "commit", "-m", f"agent: {job['task_id']}"], cwd=repo_dir, check=True)
    run_command(["git", "push", "--set-upstream", "origin", job["ref"]], cwd=repo_dir, check=True)
    pr = run_command(["gh", "pr", "create", "--draft", "--repo", job["repo"], "--head", job["ref"],
                      "--title", f"Agent task: {job['task_id']}",
                      "--body", "Created by an isolated Alfred Fly worker. Production deployment was not attempted."], cwd=repo_dir)
    print(pr.stdout); return pr.returncode


if __name__ == "__main__":
    raise SystemExit(main())
