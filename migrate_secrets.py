#!/usr/bin/env python3
"""One-shot: split the kitchen-sink .env into a runtime layer and a vault.

Why this exists
---------------
A bulk push from the old Mac dropped ~30 projects' .env files into alfred's own
.env. Three things went wrong at once:

1. Every value is loaded into the bridge's os.environ, and the claude subprocess
   inherits it — so `VERCEL=1`, `PORT`, `HOST` and a localhost `DATABASE_URL`
   from unrelated projects were in the environment of every command any agent
   ran on this box. That is the same class of failure as the stray
   ANTHROPIC_API_KEY that once diverted every session onto Console billing,
   only broader.
2. One flat namespace for thirty projects means generic keys collide.
   Two projects with a DATABASE_URL silently overwrite each other.
3. Roughly half the entries are not credentials at all — booleans, ports, model
   names, and 32 blocks of prose description — which buried the real secrets.

After this runs:
  .env       only what alfred's own processes read. Loaded into the environment.
  vault.json everything else, grouped by the project it came from. NOT loaded
             into any environment; reached only through secretbox.py.

Reversible: .secret-migration/ holds the pre-migration copies.
"""
import json
import os
import re
import shutil
import tempfile
from datetime import datetime, timezone
from pathlib import Path

ALFRED = Path("/home/ubuntu/alfred")
ENV = ALFRED / ".env"
CUSTOM = ALFRED / "custom_slots.json"
VAULT = ALFRED / "vault.json"
BACKUP = ALFRED / ".secret-migration"

NOW = datetime.now(timezone.utc).isoformat(timespec="seconds")

# --- what stays in the process environment -------------------------------
# Derived from an actual grep of os.environ reads in alfred/*.py, not guessed.
RUNTIME_EXACT = {
    "OPENAI_API_KEY", "GROQ_API_KEY", "OPENROUTER_API_KEY", "GEMINI_API_KEY",
    "OPS_BOT_TOKEN", "SECRETBOX_INGEST_TOKEN", "SUPABASE_ACCESS_TOKEN",
    "NAPKIN_BIN", "STATUS_SLUG",
}
RUNTIME_PREFIX = ("BRIDGE_", "SECRETBOX_", "MEMORY_DREAM_", "DASH_")


def is_runtime(key: str) -> bool:
    return key in RUNTIME_EXACT or key.startswith(RUNTIME_PREFIX)


# --- what gets destroyed rather than archived ----------------------------
# Only two grounds: the service actively rejected the credential, or the stored
# value is not a credential at all. A check that merely failed to REACH the
# service proves nothing and is archived as unverified.
REJECTED = {
    "ELEVENLABS_API_KEY": "ElevenLabs answered 401 invalid_api_key",
    "UPLOADTHING_TOKEN": "UploadThing answered 401 Invalid API key",
    "MIMICLAW_TG_TOKEN": "Telegram answered 401 — bot token revoked",
    "TELEGRAM_BOT_TOKEN": "Telegram answered 401 — ai-pulse bot token revoked",
    "GOOGLE_API_KEY": "Google answered 400 API_KEY_INVALID",
    "STORYCUT_GOOGLE_API_KEY": "Google answered 400 API_KEY_INVALID",
    "NEXT_PUBLIC_SUPABASE_ANON_KEY": "skyhawk Supabase project no longer resolves in DNS",
    "SUPABASE_SERVICE_ROLE_KEY": "skyhawk Supabase project no longer resolves in DNS",
}
NOT_A_CREDENTIAL = {
    "APTBOT_TOKEN": "holds a fragment of prose, not a token",
    "VERCEL_OIDC_TOKEN": "12-hour deployment JWT, expired 2026-06-26",
    "NOTE_THE_DESCRIPTION_GIVES_ONLY_A_PROJECT_CONTEXT_DARKMARINERBAC":
        "a sentence that was stored as a secret name",
}
# Dead, but alfred's own code reads them: keep the key so the Secretbox page
# still shows the slot, blank the value so nothing pretends to work.
BLANK_KEEP = {
    "OPENAI_API_KEY": "OpenAI answered 401 — replace via Secretbox",
    "GROQ_API_KEY": "Groq answered 401 — replace via Secretbox",
    "OPENROUTER_API_KEY": "OpenRouter answered 401 — replace via Secretbox",
}

# Verified live on 2026-08-01, recorded so the vault is not just a graveyard.
LIVE = {"AIRTABLE_API_KEY", "BRAVE_SEARCH_API_KEY", "BRIDGE_BOT_TOKEN",
        "GEMINI_API_KEY", "OPS_BOT_TOKEN", "PERSONAL_API_KEY_FOR_SENTRY",
        "PEXELS_API_KEY", "REPLICATE_API_TOKEN", "SERPER_API_KEY",
        "SUPABASE_ACCESS_TOKEN", "TAVILY_API_KEY", "VERCEL_TOKEN",
        "YOUTUBE_REFRESH_TOKEN", "MIMICLAW_SEARCH_KEY"}
NOTE = {"XAI_API_KEY": "key is valid; the xAI team is out of credit"}


def read_env(path: Path) -> dict:
    out = {}
    for line in path.read_text(encoding="utf-8").splitlines():
        if "=" in line and not line.lstrip().startswith("#"):
            k, v = line.split("=", 1)
            out[k.strip()] = v.strip()
    return out


def atomic_write(path: Path, text: str, mode: int = 0o600) -> None:
    fd, tmp = tempfile.mkstemp(dir=str(path.parent))
    try:
        os.write(fd, text.encode())
        os.close(fd)
        os.chmod(tmp, mode)
        os.replace(tmp, path)
    except Exception:
        os.unlink(tmp)
        raise


def main() -> None:
    env = read_env(ENV)
    custom = json.loads(CUSTOM.read_text(encoding="utf-8"))

    # The bulk push wrote each project's keys followed by a "context: <name>"
    # marker holding a prose description. Walk it in order and the blocks fall
    # out; that ordering is the only record of which key belonged to which
    # project, so it is reconstructed before anything is rewritten.
    projects, block, order = {}, [], []
    for slot_key, meta in custom.items():
        label = meta.get("label", "")
        ek = meta.get("env_key", "")
        if label.startswith("context"):
            name = label.split(":", 1)[1].strip() if ":" in label else label
            projects[name] = {"description": env.get(ek, "")[:400],
                              "keys": list(block)}
            order.append(name)
            block = []
        else:
            block.append(ek)
    if block:                      # anything after the last marker
        projects["unfiled"] = {"description": "", "keys": list(block)}
        order.append("unfiled")

    marker_keys = {m["env_key"] for m in custom.values()
                   if m.get("label", "").startswith("context")}
    owner = {k: p for p in order for k in projects[p]["keys"]}

    # --- build the vault --------------------------------------------------
    vault = {"version": 1, "migrated": NOW,
             "note": "Archived project credentials. NOT loaded into any process "
                     "environment — read them with secretbox.py --pipe/--run.",
             "projects": {}}
    deleted, blanked, kept_runtime = [], [], []

    for name in order:
        entries, desc = {}, projects[name]["description"]
        for key in projects[name]["keys"]:
            if key in marker_keys or key not in env:
                continue
            if key in REJECTED or key in NOT_A_CREDENTIAL:
                deleted.append((key, name,
                                REJECTED.get(key) or NOT_A_CREDENTIAL[key]))
                continue
            if is_runtime(key):
                # Recorded as a pointer, never a second copy of the value.
                entries[key] = {"ref": "env", "note": "lives in alfred/.env"}
                continue
            e = {"value": env[key], "added": NOW}
            e["status"] = ("live" if key in LIVE else
                           "unverified" if key.endswith(("_KEY", "_TOKEN", "_SECRET",
                                                         "_PASSWORD", "_URL"))
                           else "config")
            if key in NOTE:
                e["note"] = NOTE[key]
            entries[key] = e
        if entries or desc:
            vault["projects"][name] = {"description": desc, "entries": entries}

    # Anything in .env that no project block claimed.
    loose = {}
    for key, val in env.items():
        if key in owner or key in marker_keys:
            continue
        if is_runtime(key):
            kept_runtime.append(key)
            continue
        if key in REJECTED or key in NOT_A_CREDENTIAL:
            deleted.append((key, "alfred", REJECTED.get(key) or NOT_A_CREDENTIAL[key]))
            continue
        loose[key] = {"value": val, "added": NOW,
                      "status": "live" if key in LIVE else "unverified"}
    if loose:
        vault["projects"]["alfred (unfiled)"] = {
            "description": "Held by alfred but read by no code on this box.",
            "entries": loose}

    # --- rewrite .env -----------------------------------------------------
    lines = ["# alfred runtime configuration.",
             "#",
             "# Only variables alfred's own processes read belong here: everything in",
             "# this file is loaded into os.environ and inherited by every command an",
             "# agent runs. Archived project credentials live in vault.json and are",
             "# reached with `secretbox.py --pipe KEY -- cmd` — do not move them back.",
             f"# Split out of the old kitchen-sink .env on {NOW[:10]}.",
             ""]
    for key in sorted(env):
        if not is_runtime(key):
            continue
        if key in BLANK_KEEP:
            lines.append(f"# {BLANK_KEEP[key]}")
            lines.append(f"{key}=")
            blanked.append(key)
        else:
            lines.append(f"{key}={env[key]}")
            if key not in kept_runtime:
                kept_runtime.append(key)

    atomic_write(VAULT, json.dumps(vault, ensure_ascii=False, indent=1))
    atomic_write(ENV, "\n".join(lines) + "\n")

    # custom_slots.json drove the web page; with 224 rows it was unusable on a
    # phone. The page now renders the built-in slots plus a vault summary, so
    # the file is retired rather than edited.
    shutil.move(str(CUSTOM), str(BACKUP / "custom_slots.retired.json"))

    n_vault = sum(len(p["entries"]) for p in vault["projects"].values())
    print(f"runtime .env      : {len(kept_runtime)} keys ({len(blanked)} blanked as dead)")
    print(f"vault.json        : {n_vault} entries across {len(vault['projects'])} projects")
    print(f"deleted outright  : {len(deleted)}")
    for k, p, why in deleted:
        print(f"   - {k:<34} [{p}] {why}")
    print(f"blanked, awaiting replacement: {', '.join(blanked)}")


if __name__ == "__main__":
    main()
