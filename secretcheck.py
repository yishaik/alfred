#!/usr/bin/env python3
"""secretcheck — prove every stored credential still works.

The inventory in secretbox.py answers "what do we hold and how old is it".
This answers the harder question: "does it still authenticate?" A key that was
revoked upstream looks identical on disk to a live one, so age is no proxy for
validity — only a round trip to the service is.

Values never leave this process: each check gets the secret through the
environment of a curl subprocess, and only the HTTP status comes back.

    python3 secretcheck.py            # everything verifiable
    python3 secretcheck.py openai groq
    python3 secretcheck.py --json
"""
import concurrent.futures as cf
import json
import os
import subprocess
import sys
import urllib.error
import urllib.request
from pathlib import Path

ALFRED = Path(__file__).resolve().parent
ENV_FILE = ALFRED / ".env"


VAULT_FILE = ALFRED / "vault.json"


def load_env() -> dict:
    """Both layers, flattened for lookup: the runtime .env and the vault.

    The vault is the larger half — most credentials this box holds belong to
    projects that run elsewhere — so a checker that read only .env would report
    almost everything as "not set" and quietly stop verifying anything."""
    env = {}
    try:
        vault = json.loads(VAULT_FILE.read_text(encoding="utf-8"))
        for proj in vault.get("projects", {}).values():
            for key, entry in proj.get("entries", {}).items():
                if "value" in entry:
                    env.setdefault(key, entry["value"])
    except Exception:
        pass
    for line in ENV_FILE.read_text(encoding="utf-8").splitlines():
        if "=" in line and not line.lstrip().startswith("#"):
            k, v = line.split("=", 1)
            if v.strip():
                env[k.strip()] = v.strip().strip("\"'")   # runtime wins
    return env


def curl(url: str, headers: list[str], env: dict, method: str = "GET",
         data: str | None = None, timeout: int = 25) -> tuple[int, str]:
    """Return (http_status, short_body).

    urllib rather than a curl subprocess on purpose: a secret passed as a `-H`
    argument is visible in `ps` to every process on the box for the life of the
    request. Header templates use `$S`, substituted here from `env` so the value
    stays inside this process."""
    hdrs = {}
    for h in headers:
        k, _, v = h.partition(":")
        for name, val in env.items():
            v = v.replace("$" + name, val)
        hdrs[k.strip()] = v.strip()
    body = data.encode() if data else None
    if body:
        hdrs.setdefault("content-type", "application/json")
    # Several of these APIs sit behind Cloudflare, which answers the default
    # "Python-urllib/3.x" agent with a 403 that is indistinguishable from a
    # revoked key. Ask like a normal client so the status means what it says.
    hdrs.setdefault("User-Agent", "curl/8.5.0")
    hdrs.setdefault("Accept", "*/*")
    req = urllib.request.Request(url, data=body, headers=hdrs, method=method)
    try:
        with urllib.request.urlopen(req, timeout=timeout) as r:
            return r.status, r.read(200).decode("utf-8", "replace")
    except urllib.error.HTTPError as e:
        return e.code, e.read(200).decode("utf-8", "replace")
    except Exception as e:
        return 0, type(e).__name__


# name -> (env keys it needs, check fn). A check returns (ok, detail).
def _simple(url, hdr, ok=(200,), **kw):
    def f(v):
        code, _ = curl(url, [hdr], {"S": v[0]}, **kw)
        return code in ok, f"HTTP {code}"
    return f


CHECKS: dict[str, tuple[list[str], object, str]] = {
    "OPENAI_API_KEY": (["OPENAI_API_KEY"],
        _simple("https://api.openai.com/v1/models", "Authorization: Bearer $S"), "OpenAI"),
    "GROQ_API_KEY": (["GROQ_API_KEY"],
        _simple("https://api.groq.com/openai/v1/models", "Authorization: Bearer $S"), "Groq"),
    "OPENROUTER_API_KEY": (["OPENROUTER_API_KEY"],
        _simple("https://openrouter.ai/api/v1/key", "Authorization: Bearer $S"), "OpenRouter"),
    "XAI_API_KEY": (["XAI_API_KEY"],
        _simple("https://api.x.ai/v1/models", "Authorization: Bearer $S"), "xAI / Grok"),
    "GEMINI_API_KEY": (["GEMINI_API_KEY"],
        _simple("https://generativelanguage.googleapis.com/v1beta/models",
                "x-goog-api-key: $S"), "Google Gemini"),
    "GOOGLE_API_KEY": (["GOOGLE_API_KEY"],
        _simple("https://generativelanguage.googleapis.com/v1beta/models",
                "x-goog-api-key: $S"), "Google (generic)"),
    "STORYCUT_GOOGLE_API_KEY": (["STORYCUT_GOOGLE_API_KEY"],
        _simple("https://generativelanguage.googleapis.com/v1beta/models",
                "x-goog-api-key: $S"), "Google — storycut"),
    "ELEVENLABS_API_KEY": (["ELEVENLABS_API_KEY"],
        _simple("https://api.elevenlabs.io/v1/user", "xi-api-key: $S"), "ElevenLabs"),
    "REPLICATE_API_TOKEN": (["REPLICATE_API_TOKEN"],
        _simple("https://api.replicate.com/v1/account", "Authorization: Bearer $S"), "Replicate"),
    "PEXELS_API_KEY": (["PEXELS_API_KEY"],
        _simple("https://api.pexels.com/v1/curated?per_page=1", "Authorization: $S"), "Pexels"),
    "TAVILY_API_KEY": (["TAVILY_API_KEY"], lambda v: (
        lambda c: (c[0] in (200, 400), f"HTTP {c[0]}"))(
        curl("https://api.tavily.com/search", ["Authorization: Bearer $S"],
             {"S": v[0]}, "POST", '{"query":"ping","max_results":1}')), "Tavily"),
    "SERPER_API_KEY": (["SERPER_API_KEY"], lambda v: (
        lambda c: (c[0] == 200, f"HTTP {c[0]}"))(
        curl("https://google.serper.dev/search", ["X-API-KEY: $S"],
             {"S": v[0]}, "POST", '{"q":"ping"}')), "Serper"),
    "BRAVE_SEARCH_API_KEY": (["BRAVE_SEARCH_API_KEY"],
        _simple("https://api.search.brave.com/res/v1/web/search?q=ping",
                "X-Subscription-Token: $S"), "Brave Search"),
    "AIRTABLE_API_KEY": (["AIRTABLE_API_KEY"],
        _simple("https://api.airtable.com/v0/meta/whoami", "Authorization: Bearer $S"), "Airtable"),
    "SUPABASE_ACCESS_TOKEN": (["SUPABASE_ACCESS_TOKEN"],
        _simple("https://api.supabase.com/v1/projects", "Authorization: Bearer $S"), "Supabase (account)"),
    "PERSONAL_API_KEY_FOR_SENTRY": (["PERSONAL_API_KEY_FOR_SENTRY"],
        _simple("https://sentry.io/api/0/organizations/", "Authorization: Bearer $S"), "Sentry"),
    "VERCEL_TOKEN": (["VERCEL_TOKEN"],
        _simple("https://api.vercel.com/v2/user", "Authorization: Bearer $S"), "Vercel"),
    "UPLOADTHING_TOKEN": (["UPLOADTHING_TOKEN"],
        _simple("https://api.uploadthing.com/v6/getUsageInfo", "x-uploadthing-api-key: $S",
                ok=(200, 400)), "UploadThing"),
    "BRIDGE_BOT_TOKEN": (["BRIDGE_BOT_TOKEN"], lambda v: (
        lambda c: (c[0] == 200, f"HTTP {c[0]}"))(
        curl("https://api.telegram.org/bot" + v[0] + "/getMe", [], {})), "Telegram — @AlfredTheTBot"),
    "OPS_BOT_TOKEN": (["OPS_BOT_TOKEN"], lambda v: (
        lambda c: (c[0] == 200, f"HTTP {c[0]}"))(
        curl("https://api.telegram.org/bot" + v[0] + "/getMe", [], {})), "Telegram — AlfredOps"),
    "TELEGRAM_BOT_TOKEN": (["TELEGRAM_BOT_TOKEN"], lambda v: (
        lambda c: (c[0] == 200, f"HTTP {c[0]}"))(
        curl("https://api.telegram.org/bot" + v[0] + "/getMe", [], {})), "Telegram — ai-pulse"),
    "MIMICLAW_TG_TOKEN": (["MIMICLAW_TG_TOKEN"], lambda v: (
        lambda c: (c[0] == 200, f"HTTP {c[0]}"))(
        curl("https://api.telegram.org/bot" + v[0] + "/getMe", [], {})), "Telegram — mimiclaw"),
    "APTBOT_TOKEN": (["APTBOT_TOKEN"], lambda v: (
        lambda c: (c[0] == 200, f"HTTP {c[0]}"))(
        curl("https://api.telegram.org/bot" + v[0] + "/getMe", [], {})), "Telegram — aptbot"),
    "SUPABASE_SERVICE_ROLE_KEY": (["SUPABASE_SERVICE_ROLE_KEY", "NEXT_PUBLIC_SUPABASE_URL"],
        lambda v: (lambda c: (c[0] in (200, 404), f"HTTP {c[0]}"))(
            curl(v[1].rstrip("/") + "/rest/v1/", ["apikey: $S", "Authorization: Bearer $S"],
                 {"S": v[0]})), "Supabase service role (skyhawk)"),
    "NEXT_PUBLIC_SUPABASE_ANON_KEY": (["NEXT_PUBLIC_SUPABASE_ANON_KEY", "NEXT_PUBLIC_SUPABASE_URL"],
        lambda v: (lambda c: (c[0] in (200, 404), f"HTTP {c[0]}"))(
            curl(v[1].rstrip("/") + "/rest/v1/", ["apikey: $S"], {"S": v[0]})),
        "Supabase anon (skyhawk)"),
    "YOUTUBE_REFRESH_TOKEN": (["YOUTUBE_REFRESH_TOKEN", "YOUTUBE_CLIENT_ID", "YOUTUBE_CLIENT_SECRET"],
        lambda v: (lambda c: (c[0] == 200, f"HTTP {c[0]}"))(
            curl("https://oauth2.googleapis.com/token", [], {}, "POST",
                 json.dumps({"client_id": v[1], "client_secret": v[2],
                             "refresh_token": v[0], "grant_type": "refresh_token"}))),
        "YouTube OAuth refresh"),
    "N8N_API_KEY": (["N8N_API_KEY", "N8N_URL"],
        lambda v: (lambda c: (c[0] == 200, f"HTTP {c[0]}"))(
            curl(v[1].rstrip("/") + "/api/v1/workflows?limit=1", ["X-N8N-API-KEY: $S"],
                 {"S": v[0]}, timeout=12)), "n8n"),
    # The URL carries the password, so it goes to psql through the environment,
    # never as an argv element — argv is world-readable in `ps` for the whole
    # life of the connection.
    "DATABASE_URL": (["DATABASE_URL"], lambda v: (
        lambda r: (r.returncode == 0, "connected" if r.returncode == 0
                   else (r.stderr or "").strip().splitlines()[-1][:80] if r.stderr else "failed"))(
        subprocess.run(["bash", "-c", 'exec psql "$DBURL" -tAc "select 1"'],
                       capture_output=True, text=True, timeout=25,
                       env={**os.environ, "DBURL": v[0]})), "Postgres DATABASE_URL"),
}


def check_one(name):
    keys, fn, label = CHECKS[name]
    env = load_env()
    vals = [env.get(k, "") for k in keys]
    if not vals[0]:
        return name, label, None, "not set"
    missing = [k for k, v in zip(keys, vals) if not v]
    if missing:
        return name, label, None, "missing " + ", ".join(missing)
    try:
        ok, detail = fn(vals)
    except Exception as e:
        ok, detail = False, f"{type(e).__name__}: {e}"[:80]
    return name, label, ok, detail


def main(argv):
    want = [a for a in argv if not a.startswith("-")]
    names = [n for n in CHECKS if not want or n in want or
             any(w.lower() in n.lower() for w in want)]
    with cf.ThreadPoolExecutor(max_workers=10) as ex:
        results = list(ex.map(check_one, names))
    if "--json" in argv:
        print(json.dumps([{"key": n, "service": l, "ok": o, "detail": d}
                          for n, l, o, d in results], ensure_ascii=False, indent=1))
        return 0
    good = [r for r in results if r[2] is True]
    bad = [r for r in results if r[2] is False]
    skip = [r for r in results if r[2] is None]

    # Age never told us whether a key still authenticates, so the weekly job
    # runs this too and reports the ones the service itself rejected.
    if "--notify" in argv and bad:
        subprocess.run([str(ALFRED / "opsnotify.sh"),
                        "🔐 סודות שהשירות דחה:\n" +
                        "\n".join(f"· {l} ({d})" for _, l, _, d in sorted(bad)) +
                        "\n\nhttps://alfred.tailbb9b2e.ts.net/secrets"],
                       capture_output=True, timeout=25)
    for title, group, mark in (("עובד", good, "✅"), ("נכשל", bad, "❌"),
                               ("לא נבדק", skip, "○")):
        if not group:
            continue
        print(f"\n{mark} {title} ({len(group)})")
        for n, l, _, d in sorted(group):
            print(f"   {n:<32} {l:<28} {d}")
    print(f"\nסה\"כ: {len(good)} עובדים, {len(bad)} נכשלו, {len(skip)} לא נבדקו")
    return 1 if bad else 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))
