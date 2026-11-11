"""Safe deployment and repository control surface for Alfred."""
from __future__ import annotations

import argparse
import json
import os
import sys
from pathlib import Path

import httpx

from .security import (
    PolicyError,
    existing_binaries,
    production_approval,
    resolve_under,
    run_command,
    validate_provider,
    validate_ref,
    validate_repo,
)

WORKSPACE_ROOT = Path(os.environ.get("BRIDGE_WORKDIR", "/data/workspaces"))


def _print_result(result) -> int:
    print(json.dumps({"argv": list(result.argv), "returncode": result.returncode,
                      "stdout": result.stdout, "stderr": result.stderr},
                     ensure_ascii=False, indent=2))
    return result.returncode


def cmd_doctor(_args: argparse.Namespace) -> int:
    binaries = existing_binaries(["git", "gh", "claude", "codex", "gemini",
                                   "vercel", "netlify", "wrangler", "supabase", "hf"])
    env_checks = {name: bool(os.environ.get(name)) for name in [
        "BRIDGE_BOT_TOKEN", "BRIDGE_CHAT_ID", "GH_TOKEN", "XAI_API_KEY", "TAVILY_API_KEY",
        "VERCEL_TOKEN", "NETLIFY_AUTH_TOKEN", "CLOUDFLARE_API_TOKEN",
        "SUPABASE_ACCESS_TOKEN", "HF_TOKEN", "FLY_API_TOKEN"]}
    print(json.dumps({"binaries": binaries, "secrets_present": env_checks,
                      "production_deploy_enabled": os.environ.get("ALFRED_ALLOW_PRODUCTION_DEPLOY", "0") == "1",
                      "workspace_root": str(WORKSPACE_ROOT)}, indent=2))
    return 0 if all(binaries.values()) else 1


def _repo_dir(repo: str) -> Path:
    owner, name = validate_repo(repo).split("/", 1)
    return resolve_under(WORKSPACE_ROOT, WORKSPACE_ROOT / owner / name)


def cmd_clone(args: argparse.Namespace) -> int:
    repo = validate_repo(args.repo)
    target = _repo_dir(repo)
    target.parent.mkdir(parents=True, exist_ok=True)
    if (target / ".git").exists():
        result = run_command(["git", "fetch", "--prune", "origin"], cwd=target)
    elif target.exists() and any(target.iterdir()):
        raise PolicyError(f"target exists and is not an empty git checkout: {target}")
    else:
        result = run_command(["gh", "repo", "clone", repo, str(target), "--", "--filter=blob:none"])
    return _print_result(result)


def cmd_branch(args: argparse.Namespace) -> int:
    target = _repo_dir(args.repo)
    branch = validate_ref(args.branch)
    base = validate_ref(args.base)
    run_command(["git", "fetch", "origin", base], cwd=target, check=True)
    return _print_result(run_command(["git", "switch", "-C", branch, f"origin/{base}"], cwd=target))


def cmd_test(args: argparse.Namespace) -> int:
    cwd = resolve_under(WORKSPACE_ROOT, args.cwd)
    argv = json.loads(args.argv_json)
    if not isinstance(argv, list) or not argv or not all(isinstance(x, str) for x in argv):
        raise PolicyError("--argv-json must be a non-empty JSON string array")
    allowed = {"npm", "npx", "pnpm", "yarn", "python", "python3", "pytest", "uv", "go", "cargo", "dotnet", "make"}
    if Path(argv[0]).name not in allowed:
        raise PolicyError(f"test executable not allowlisted: {argv[0]}")
    return _print_result(run_command(argv, cwd=cwd))


def cmd_pr(args: argparse.Namespace) -> int:
    repo = validate_repo(args.repo)
    cwd = _repo_dir(repo)
    branch = validate_ref(args.branch)
    run_command(["git", "push", "--set-upstream", "origin", branch], cwd=cwd, check=True)
    argv = ["gh", "pr", "create", "--repo", repo, "--head", branch, "--base", args.base,
            "--title", args.title, "--body", args.body]
    if args.draft:
        argv.append("--draft")
    return _print_result(run_command(argv, cwd=cwd))


def _deploy_argv(provider: str, mode: str, cwd: Path, args: argparse.Namespace) -> list[str]:
    production = mode == "production"
    if provider == "vercel":
        argv = ["vercel", "deploy", "--yes", "--token", os.environ.get("VERCEL_TOKEN", "")]
        if production: argv.append("--prod")
        return argv
    if provider == "netlify":
        argv = ["netlify", "deploy", "--build", "--json"]
        if production: argv.append("--prod")
        return argv
    if provider == "cloudflare":
        return ["wrangler", "deploy"] if production else ["wrangler", "deploy", "--dry-run"]
    if provider == "supabase":
        return ["supabase", "db", "push", "--linked"] if production else ["supabase", "db", "lint", "--linked"]
    if provider == "huggingface":
        if not production: return ["hf", "auth", "whoami"]
        if not args.hf_repo: raise PolicyError("--hf-repo is required for Hugging Face upload")
        return ["hf", "upload", args.hf_repo, str(cwd), ".", "--repo-type", args.hf_repo_type]
    raise PolicyError(f"unsupported deploy provider: {provider}")


def cmd_deploy(args: argparse.Namespace) -> int:
    provider = validate_provider(args.provider)
    repo = validate_repo(args.repo)
    cwd = _repo_dir(repo)
    if args.mode == "production":
        production_approval(provider, repo, args.approval)
    argv = _deploy_argv(provider, args.mode, cwd, args)
    if any(x == "" for x in argv):
        raise PolicyError(f"missing credential required by {provider}")
    return _print_result(run_command(argv, cwd=cwd, timeout=1800))


def cmd_tavily(args: argparse.Namespace) -> int:
    key = os.environ.get("TAVILY_API_KEY", "")
    if not key: raise PolicyError("TAVILY_API_KEY is not configured")
    response = httpx.post("https://api.tavily.com/search", json={
        "api_key": key, "query": args.query, "search_depth": args.depth,
        "max_results": args.max_results}, timeout=60)
    response.raise_for_status()
    print(json.dumps(response.json(), ensure_ascii=False, indent=2))
    return 0


def cmd_appdeploy_manifest(args: argparse.Namespace) -> int:
    repo = validate_repo(args.repo)
    cwd = _repo_dir(repo)
    out = resolve_under(cwd, cwd / ".agent-runtime" / "appdeploy-handoff.json")
    out.parent.mkdir(parents=True, exist_ok=True)
    manifest = {"schema": "alfred.appdeploy.handoff/v1", "repository": repo,
                "source_path": str(cwd), "intent": args.intent,
                "note": "AppDeploy is a ChatGPT connector, not a public runtime API. Use this manifest in an AppDeploy-enabled ChatGPT session."}
    out.write_text(json.dumps(manifest, ensure_ascii=False, indent=2), encoding="utf-8")
    print(str(out))
    return 0


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="alfred-platform")
    sub = parser.add_subparsers(dest="command", required=True)
    sub.add_parser("doctor").set_defaults(func=cmd_doctor)
    p = sub.add_parser("clone"); p.add_argument("repo"); p.set_defaults(func=cmd_clone)
    p = sub.add_parser("branch"); p.add_argument("repo"); p.add_argument("branch"); p.add_argument("--base", default="master"); p.set_defaults(func=cmd_branch)
    p = sub.add_parser("test"); p.add_argument("--cwd", required=True); p.add_argument("--argv-json", required=True); p.set_defaults(func=cmd_test)
    p = sub.add_parser("pr"); p.add_argument("repo"); p.add_argument("branch"); p.add_argument("--base", default="master"); p.add_argument("--title", required=True); p.add_argument("--body", required=True); p.add_argument("--draft", action="store_true", default=True); p.set_defaults(func=cmd_pr)
    p = sub.add_parser("deploy"); p.add_argument("provider", choices=["vercel", "netlify", "cloudflare", "supabase", "huggingface"]); p.add_argument("repo"); p.add_argument("--mode", choices=["preview", "production"], default="preview"); p.add_argument("--approval"); p.add_argument("--hf-repo"); p.add_argument("--hf-repo-type", choices=["model", "dataset", "space"], default="space"); p.set_defaults(func=cmd_deploy)
    p = sub.add_parser("tavily"); p.add_argument("query"); p.add_argument("--depth", choices=["basic", "advanced"], default="advanced"); p.add_argument("--max-results", type=int, default=8); p.set_defaults(func=cmd_tavily)
    p = sub.add_parser("appdeploy-manifest"); p.add_argument("repo"); p.add_argument("--intent", default="deploy application from reviewed repository state"); p.set_defaults(func=cmd_appdeploy_manifest)
    return parser


def main() -> int:
    try:
        args = build_parser().parse_args()
        return args.func(args)
    except (PolicyError, json.JSONDecodeError) as exc:
        print(json.dumps({"error": str(exc)}, ensure_ascii=False), file=sys.stderr)
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
