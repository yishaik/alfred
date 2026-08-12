#!/usr/bin/env python3
"""Secretbox — paste a secret from your phone straight onto the box.

The problem this solves: sending a token through the Telegram chat puts it on
Telegram's servers and inside the agent's transcript, which is exactly what
forces the rotation in the first place. Here the value travels phone → tailnet →
disk and is never echoed, never logged, and never enters the agent's context.
The agent only ever sees a SHA-256 fingerprint.

Bound to 127.0.0.1 and published to the tailnet with:
    tailscale serve --bg --https 443 --set-path /secrets http://127.0.0.1:8770

Python stdlib only, matching the understudy dashboard's style.
"""
import hashlib
import hmac
import html
import json
import os
import re
import subprocess
import tempfile
import time
from datetime import datetime, timezone
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import parse_qs

HOST = os.environ.get("SECRETBOX_HOST", "127.0.0.1")
PORT = int(os.environ.get("SECRETBOX_PORT", "8770"))
# tailscale serve injects this; empty means "anyone on the tailnet", which is
# already only this user's own devices.
EXPECT_LOGIN = os.environ.get("SECRETBOX_LOGIN", "").strip()

ALFRED = Path("/home/ubuntu/alfred")

# The registry is also the inventory: every secret we hold, where it lives, how
# to replace it, and how to prove the new one works.
SLOTS = {
    "supabase": {
        "label": "Supabase access token",
        "kind": "env",
        "path": ALFRED / ".env",
        "key": "SUPABASE_ACCESS_TOKEN",
        "prefix": "sbp_",
        # The API directly, not the CLI. `supabase login --token` accepted the
        # value and then `projects list` answered Unauthorized, because the CLI
        # reads its own credential store rather than what was just handed to it
        # — so a perfectly good token was rejected and never written. Worse, the
        # login half MUTATED the machine's stored credentials as a side effect
        # of checking a candidate. A verifier must not change state.
        # Several endpoints, because /v1/projects is on its way out — the CLI
        # names its failure LegacyProjectsListUnexpectedStatusError — and a
        # verifier pinned to a deprecated route rejects perfectly good new
        # tokens. Any one of these answering 200 proves the token works; the
        # failure message lists what each said, so the next change is informed
        # rather than guessed.
        "verify": ["bash", "-lc", """
for ep in organizations projects profile; do
  code=$(curl -s -o /dev/null -w '%{http_code}' --max-time 20 \
    -H "Authorization: Bearer $SECRET" -A 'curl/8.5.0' \
    "https://api.supabase.com/v1/$ep")
  [ "$code" = 200 ] && exit 0
  msg="$msg $ep=$code"
done
echo "no endpoint accepted it:$msg"; exit 1
"""],
        "note": "גישה מלאה לכל הפרויקטים, כולל TLV-quest בפרודקשן",
    },
    "herenow": {
        "label": "here.now API key",
        "kind": "file",
        "path": Path.home() / ".herenow/credentials",
        "verify": ["bash", "-lc", "curl -sSf -H \"Authorization: Bearer $SECRET\" https://here.now/api/v1/accounts >/dev/null"],
        "note": "מפרסם ומעדכן את דפי הדוחות",
    },
    "openai": {
        "label": "OpenAI API key",
        "kind": "env",
        "path": ALFRED / ".env",
        "key": "OPENAI_API_KEY",
        "prefix": "sk-",
        "verify": ["bash", "-lc", "curl -sSf -H \"Authorization: Bearer $SECRET\" https://api.openai.com/v1/models >/dev/null"],
        "note": "TTS ותמלול בגשר",
    },
    "groq": {
        "label": "Groq API key",
        "kind": "env",
        "path": ALFRED / ".env",
        "key": "GROQ_API_KEY",
        "prefix": "gsk_",
        "verify": ["bash", "-lc", "curl -sSf -H \"Authorization: Bearer $SECRET\" https://api.groq.com/openai/v1/models >/dev/null"],
        "note": "תמלול מהיר",
    },
    "openrouter": {
        "label": "OpenRouter API key",
        "kind": "env",
        "path": ALFRED / ".env",
        "key": "OPENROUTER_API_KEY",
        "prefix": "sk-or-",
        "verify": ["bash", "-lc", "curl -sSf -H \"Authorization: Bearer $SECRET\" https://openrouter.ai/api/v1/key >/dev/null"],
        "note": "ניתוב מודלים שאינם Claude",
    },
    "gemini": {
        "label": "Google Gemini API key",
        "kind": "env",
        "path": ALFRED / ".env",
        "key": "GEMINI_API_KEY",
        "prefix": None,
        "verify": ["bash", "-lc", "curl -sSf -H \"x-goog-api-key: $SECRET\" https://generativelanguage.googleapis.com/v1beta/models >/dev/null"],
        "note": "הספק החינמי הראשון בנתב המודלים",
    },
    # The bot tokens are here so a BotFather rotation can land without anyone
    # editing .env by hand. Both need a bridge restart to take effect.
    "bridgebot": {
        "label": "טוקן הבוט של אלפרד (@AlfredTheTBot)",
        "kind": "env",
        "path": ALFRED / ".env",
        "key": "BRIDGE_BOT_TOKEN",
        "prefix": None,
        "verify": ["bash", "-lc", "curl -sSf https://api.telegram.org/bot$SECRET/getMe >/dev/null"],
        "note": "הגשר יופעל מחדש אוטומטית אחרי שמירה",
        "restart": "alfred.service",
    },
    "opsbot": {
        "label": "טוקן הבוט של AlfredOps",
        "kind": "env",
        "path": ALFRED / ".env",
        "key": "OPS_BOT_TOKEN",
        "prefix": None,
        "verify": ["bash", "-lc", "curl -sSf https://api.telegram.org/bot$SECRET/getMe >/dev/null"],
        "note": "התראות תפעול בלבד · הגשר יופעל מחדש אוטומטית",
        "restart": "alfred.service",
    },
}


# Published pages and their passwords. These live here rather than in a pinned
# Telegram message for the same reason the tokens do: the tailnet page is the
# one channel that does not persist a copy anywhere the agent or Telegram can
# read. AlfredOps carries the links; the passwords stay here.
PAGES_FILE = ALFRED / "pages.json"

# The vault: every credential this box holds but does not itself run on.
#
# It is deliberately NOT the .env. Anything in .env is loaded into the bridge's
# os.environ and inherited by every command an agent runs, so thirty projects'
# worth of keys sitting there meant a stray PORT / DATABASE_URL / VERCEL=1 in
# the environment of unrelated work, and generic names silently overwriting each
# other across projects. The vault is grouped by project, so DATABASE_URL can
# exist twice without collision, and nothing here reaches a process environment
# unless a --pipe/--run call puts it there for one child.
VAULT_FILE = ALFRED / "vault.json"

# Config keys that are not secrets. Overwriting one of these from a paste box
# would quietly break the bridge (wrong chat, wrong workdir, wrong lock port),
# so the free-form form refuses them by name.
PROTECTED = {"BRIDGE_CHAT_ID", "BRIDGE_GROUP_ID", "BRIDGE_WORKDIR", "BRIDGE_STATE_DIR",
             "BRIDGE_ENV_FILE", "BRIDGE_LOCK_PORT", "BRIDGE_PEER_PORT", "BRIDGE_PEER_NAME",
             "BRIDGE_PEERS", "BRIDGE_PEER_BIND", "BRIDGE_BACKUP_REPO", "BRIDGE_BACKUP_DIR",
             "BRIDGE_CLAUDE_BIN"}


def load_vault() -> dict:
    """Read the vault, or fail. An empty dict is returned ONLY for a vault that
    does not exist yet.

    This used to swallow every exception and return an empty vault. Combined
    with the read-modify-write in vault_put, that turned an *unreadable* file
    into a silent total erase: a process that could not read the vault wrote
    back one containing only its own new entry, and ~230 credentials went in a
    single call with no error. A permission error must stop the caller, not be
    mistaken for "there is nothing here yet".
    """
    if not VAULT_FILE.exists():
        return {"version": 1, "projects": {}}
    try:
        return json.loads(VAULT_FILE.read_text(encoding="utf-8"))
    except OSError as e:
        raise RuntimeError(
            f"cannot read {VAULT_FILE}: {e}. Refusing to continue — writing now "
            f"would replace the vault with whatever this process holds."
        ) from e
    except json.JSONDecodeError as e:
        raise RuntimeError(
            f"{VAULT_FILE} is not valid JSON: {e}. Restore it before writing; "
            f"a write now would discard every entry it still contains."
        ) from e


def save_vault(d: dict) -> None:
    # A vault that shrinks is almost always a bug rather than an intention:
    # entries are removed one at a time, never in bulk. Refuse a write that
    # drops more than a quarter of what is on disk unless it is explicit.
    if VAULT_FILE.exists() and not os.environ.get("SECRETBOX_ALLOW_SHRINK"):
        try:
            was = json.loads(VAULT_FILE.read_text(encoding="utf-8"))
        except Exception:
            was = None
        if was is not None:
            def _count(v):
                return sum(len(p.get("entries", {}))
                           for p in v.get("projects", {}).values())
            before, after = _count(was), _count(d)
            if before and after < before * 0.75:
                raise RuntimeError(
                    f"refusing to shrink the vault from {before} to {after} "
                    f"entries. Set SECRETBOX_ALLOW_SHRINK=1 if that is really "
                    f"what you mean."
                )

    fd, tmp = tempfile.mkstemp(dir=str(VAULT_FILE.parent))
    try:
        os.write(fd, json.dumps(d, ensure_ascii=False, indent=1).encode())
        os.close(fd)
        os.chmod(tmp, 0o600)
        os.replace(tmp, VAULT_FILE)
    except Exception:
        os.unlink(tmp)
        raise


def vault_find(name: str) -> tuple[str | None, str, list[str]]:
    """Resolve KEY or PROJECT/KEY out of the vault.

    Returns (value, where, candidates). A bare KEY that exists under several
    projects resolves to nothing and reports the candidates instead of guessing
    — picking one silently is how the old flat store lost values in the first
    place."""
    vault = load_vault()
    proj_want, _, key = name.rpartition("/")
    hits = []
    for pname, p in vault.get("projects", {}).items():
        e = p.get("entries", {}).get(key)
        if e is None or "value" not in e:
            continue
        if proj_want and proj_want.lower() not in pname.lower():
            continue
        hits.append((pname, e["value"]))
    if len(hits) == 1:
        return hits[0][1], f"vault:{hits[0][0]}", []
    if not hits:
        return None, "", []
    return None, "", [f"{p}/{key}" for p, _ in hits]


# Token shapes are the most reliable signal there is — far better than parsing a
# description, and it works whatever language the description is written in.
SIGNATURES = [
    ("sk-ant-", "ANTHROPIC_API_KEY"), ("sk-or-", "OPENROUTER_API_KEY"),
    ("sk-proj-", "OPENAI_API_KEY"), ("sk-", "OPENAI_API_KEY"),
    ("sbp_", "SUPABASE_ACCESS_TOKEN"), ("sbsecret_", "SUPABASE_SERVICE_KEY"),
    ("ghp_", "GITHUB_TOKEN"), ("gho_", "GITHUB_TOKEN"), ("github_pat_", "GITHUB_TOKEN"),
    ("gsk_", "GROQ_API_KEY"), ("xoxb-", "SLACK_BOT_TOKEN"), ("xoxp-", "SLACK_USER_TOKEN"),
    ("re_", "RESEND_API_KEY"), ("SG.", "SENDGRID_API_KEY"),
    ("sk_live_", "STRIPE_SECRET_KEY"), ("sk_test_", "STRIPE_TEST_KEY"),
    ("rk_live_", "STRIPE_RESTRICTED_KEY"), ("pk_live_", "STRIPE_PUBLISHABLE_KEY"),
    ("AKIA", "AWS_ACCESS_KEY_ID"), ("AIza", "GOOGLE_API_KEY"),
    ("hf_", "HUGGINGFACE_TOKEN"), ("tvly-", "TAVILY_API_KEY"),
    ("nvapi-", "NVIDIA_API_KEY"), ("pplx-", "PERPLEXITY_API_KEY"),
    ("fly_", "FLY_API_TOKEN"), ("vercel_", "VERCEL_TOKEN"), ("dop_v1_", "DIGITALOCEAN_TOKEN"),
    ("glpat-", "GITLAB_TOKEN"), ("shpat_", "SHOPIFY_ACCESS_TOKEN"),
    ("ntn_", "NOTION_TOKEN"), ("secret_", "NOTION_TOKEN"),
    ("lin_api_", "LINEAR_API_KEY"), ("brd-", "BRIGHTDATA_TOKEN"),
]


def key_from_value(value: str) -> str | None:
    for pref, key in SIGNATURES:
        if value.startswith(pref):
            return key
    if re.fullmatch(r"\d{8,12}:[A-Za-z0-9_-]{30,}", value):
        return "TELEGRAM_BOT_TOKEN"
    return None


def key_from_description(text: str) -> str | None:
    """Ask the local Claude to name it. Only the description travels — never the
    secret. Used only when the value's own shape says nothing."""
    binp = os.environ.get("BRIDGE_CLAUDE_BIN") or "/home/ubuntu/.local/bin/claude"
    if not Path(binp).exists():
        return None
    prompt = (
        "Convert this description of a credential into a single canonical "
        "environment-variable name in SCREAMING_SNAKE_CASE. The description may be "
        "in Hebrew. Use the conventional English name for the service if you can "
        "identify it (e.g. Twilio auth token -> TWILIO_AUTH_TOKEN). Reply with the "
        "name only, nothing else.\n\nDescription: " + text[:200])
    try:
        r = subprocess.run([binp, "-p", prompt], capture_output=True, text=True,
                           timeout=60, env={**os.environ,
                                            "PATH": os.environ.get("PATH", "") + ":/home/ubuntu/.local/bin"})
        out = (r.stdout or "").strip().splitlines()
        cand = re.sub(r"[^A-Z0-9_]", "", (out[-1] if out else "").upper().replace(" ", "_"))
        return cand[:64] or None
    except Exception:
        return None


def env_key(name: str, value: str = "") -> tuple[str, str]:
    """Return (env_key, how_it_was_chosen)."""
    if value:
        k = key_from_value(value)
        if k:
            return k, "זוהה לפי חתימת הערך"
    k = key_from_description(name)
    if k:
        return k, "נגזר מהתיאור שכתבת"
    slug = re.sub(r"_+", "_", re.sub(r"[^A-Za-z0-9]+", "_", name.strip()).strip("_").upper())
    return (slug[:64] or "CUSTOM_SECRET"), "נגזר מהטקסט כפי שהוא"


def all_slots() -> dict:
    """The runtime slots — the credentials alfred's own processes read.

    Everything else lives in the vault and is summarised separately. The page
    used to render one card per stored secret; after ~230 arrived from a bulk
    push that was unusable on a phone, which is the point of the split."""
    return dict(SLOTS)


def load_pages() -> list[dict]:
    """Hand-written entries, plus every here.now site whose password we hold.

    The here.now half is DERIVED, never stored here. When these four sites were
    listed in pages.json with their passwords copied in by hand, changing a
    password on here.now left this page serving the old one with no signal —
    the page looked authoritative and was wrong. Reading the vault means the
    displayed password is the password, or the row does not appear at all.
    """
    try:
        pages = json.loads(PAGES_FILE.read_text(encoding="utf-8"))
    except Exception:
        pages = []
    pages = [p for p in pages if ".here.now/" not in (p.get("url") or "")]

    try:
        entries = load_vault().get("projects", {}).get("here-now", {}).get("entries", {})
        meta = json.loads((ALFRED / "state" / "herenow-sites.json")
                          .read_text(encoding="utf-8")).get("sites", {})
    except Exception:
        return pages

    # Show when each page was last published. A here.now Site is a static
    # snapshot: without a date on the row there is nothing to distinguish a
    # report regenerated an hour ago from one frozen since last week, and the
    # second kind is the one that quietly misleads.
    import datetime as _dt
    today = _dt.date.today()

    def age(iso: str, verified: bool) -> str:
        try:
            d = _dt.datetime.fromisoformat(iso.replace("Z", "+00:00")).date()
        except Exception:
            return ""
        days = (today - d).days
        # Until the watcher has seen a version change for a site, all we have is
        # updatedAt — which also moves on a password or metadata edit. Say
        # "touched" for that, and "content" only once we know it is the content.
        verb = "התוכן עודכן" if verified else "שינוי אחרון"
        if days <= 0:
            return f"{verb} היום"
        if days == 1:
            return f"{verb} אתמול"
        if days < 7:
            return f"{verb} לפני {days} ימים"
        return f"⚠️ {verb} לפני {days} ימים · {d.isoformat()}"

    sites = []
    for key, entry in entries.items():
        if not key.startswith("SITE_") or "value" not in entry:
            continue
        slug = key[5:].lower().replace("_", "-")
        info = meta.get(slug, {})
        sites.append({"name": info.get("name") or slug,
                      "url": info.get("url") or f"https://{slug}.here.now/",
                      "password": entry["value"],
                      "note": age(info.get("content_at") or info.get("updated") or "",
                                  bool(info.get("content_at"))) or slug})
    sites.sort(key=lambda p: p["name"])
    return pages + sites


def fingerprint(value: str) -> str:
    return "sha256:" + hashlib.sha256(value.encode()).hexdigest()[:12]


def esc(s) -> str:
    """HTML-escape anything interpolated into the page.

    Not paranoia: project and key names reach this page from the /ingest
    endpoint and from the free-form form, so they are attacker-controlled the
    moment the ingest token is. Script running on THIS page can read the
    password fields the user is about to paste a secret into, which makes an
    injection here worth more than any single stored credential."""
    return html.escape(str(s), quote=True)


def token_eq(a: str, b: str) -> bool:
    """Constant-time compare for anything bearer-shaped."""
    return bool(a) and bool(b) and hmac.compare_digest(a, b)


def read_current(slot: dict) -> str | None:
    p = slot["path"]
    try:
        if slot["kind"] == "file":
            return p.read_text(encoding="utf-8").strip() or None
        for line in p.read_text(encoding="utf-8").splitlines():
            if line.startswith(slot["key"] + "="):
                return line.split("=", 1)[1].strip().strip("\"'") or None
    except FileNotFoundError:
        return None
    return None


def write_secret(slot: dict, value: str) -> None:
    """Atomic write, 600, never through a shell."""
    p = slot["path"]
    p.parent.mkdir(parents=True, exist_ok=True)
    if slot["kind"] == "file":
        new = value
    else:
        lines, found = [], False
        if p.exists():
            for line in p.read_text(encoding="utf-8").splitlines():
                if line.startswith(slot["key"] + "="):
                    lines.append(f"{slot['key']}={value}")
                    found = True
                else:
                    lines.append(line)
        if not found:
            lines.append(f"{slot['key']}={value}")
        new = "\n".join(lines) + "\n"

    fd, tmp = tempfile.mkstemp(dir=str(p.parent))
    try:
        os.write(fd, new.encode())
        os.close(fd)
        os.chmod(tmp, 0o600)
        os.replace(tmp, p)
    except Exception:
        os.unlink(tmp)
        raise


def restart_after(slot: dict) -> str:
    """Restart the service that only reads this value at startup.

    Without this, replacing the bot token is a trap: revoking it in BotFather
    kills the bridge instantly, so the moment you have the new value you have
    also lost the channel for asking anyone to load it. Secretbox is reachable
    over the tailnet with no dependency on Telegram or on the bridge process,
    which makes it the one place that can close the loop by itself.
    """
    unit = slot.get("restart")
    if not unit:
        return ""
    try:
        r = subprocess.run(["sudo", "-n", "systemctl", "restart", unit],
                           capture_output=True, timeout=90)
        if r.returncode == 0:
            return f" · {unit} הופעל מחדש"
        return f" · ההפעלה מחדש של {unit} נכשלה — הרץ ידנית"
    except Exception:
        return f" · לא הצלחתי להפעיל מחדש את {unit} — הרץ ידנית"


def verify(slot: dict, value: str) -> tuple[bool, str]:
    cmd = slot.get("verify")
    if not cmd:
        return True, "אין בדיקה מוגדרת — נשמר בלי אימות"
    env = {**os.environ, "SECRET": value,
           "PATH": os.environ.get("PATH", "") + ":/home/ubuntu/.local/bin"}
    try:
        r = subprocess.run(cmd, env=env, capture_output=True, timeout=45)
        if r.returncode == 0:
            return True, "אומת מול השירות"
        # Say WHY. "the service rejected it" sent us hunting a broken verifier
        # when the real answer was in the response, and it gives no way to tell
        # a dead credential from a wrong endpoint or a typo in the paste.
        out = ((r.stderr or b"") + b" " + (r.stdout or b"")).decode("utf-8", "replace")
        detail = " ".join(out.split())[:160]
        return False, f"השירות דחה את הסוד — {detail}" if detail else "השירות דחה את הסוד"
    except Exception as e:
        return False, f"הבדיקה נכשלה: {type(e).__name__}"


PAGE = """<!DOCTYPE html><html lang="he" dir="rtl"><head><meta charset="utf-8">
<meta name="viewport" content="width=device-width,initial-scale=1,viewport-fit=cover">
<meta name="theme-color" content="#0b0f19"><title>Secretbox</title><style>
:root{color-scheme:light dark;
--bg:#f5f6f8;--s:#ffffff;--s2:#f8f9fb;--ink:#101828;--ink2:#475467;--mut:#98a2b3;
--grid:#e4e7ec;--ring:rgba(16,24,40,.06);--good:#12b76a;--warn:#f79009;--bad:#f04438;
--acc:#4f46e5;--acc-ink:#ffffff;--chip:#f2f4ff;--chip-ink:#4338ca}
@media(prefers-color-scheme:dark){:root{
--bg:#0b0f19;--s:#141a27;--s2:#0f1520;--ink:#f2f4f7;--ink2:#98a2b3;--mut:#667085;
--grid:#232b3a;--ring:rgba(255,255,255,.05);--acc:#7c8cff;--acc-ink:#0b0f19;
--chip:#1b2233;--chip-ink:#aeb8ff}}
*{box-sizing:border-box}
html{-webkit-text-size-adjust:100%}
body{margin:0;padding:max(20px,env(safe-area-inset-top)) 16px 64px;background:var(--bg);
color:var(--ink);font-family:system-ui,-apple-system,"Segoe UI",sans-serif;line-height:1.55;
-webkit-font-smoothing:antialiased}
.w{max-width:640px;margin:0 auto}
header{display:flex;align-items:center;gap:12px;margin:4px 0 6px}
.lock{width:40px;height:40px;flex:none;border-radius:11px;background:var(--acc);color:var(--acc-ink);
display:grid;place-items:center;font-size:1.15rem;box-shadow:0 2px 8px rgba(79,70,229,.28)}
h1{font-size:1.35rem;margin:0;letter-spacing:-.01em;font-weight:700}
.sub{color:var(--ink2);font-size:.84rem;margin:1px 0 0}
.banner{display:flex;gap:9px;align-items:flex-start;background:var(--s);border:1px solid var(--ring);
border-radius:12px;padding:11px 13px;margin:16px 0 22px;font-size:.82rem;color:var(--ink2)}
.banner .i{flex:none;opacity:.7}
.sec{font-size:.72rem;font-weight:700;letter-spacing:.08em;text-transform:uppercase;
color:var(--mut);margin:28px 2px 11px;display:flex;align-items:center;gap:10px}
.sec::after{content:"";flex:1;height:1px;background:var(--grid)}
.card{background:var(--s);border:1px solid var(--ring);border-radius:14px;padding:15px 16px;
margin-bottom:11px;box-shadow:0 1px 2px rgba(16,24,40,.04);transition:border-color .15s}
.card:focus-within{border-color:var(--acc)}
.hd{display:flex;justify-content:space-between;align-items:center;gap:8px}
.hd b{font-size:.98rem;font-weight:600;display:flex;align-items:center;gap:7px}
.st{font-size:.72rem;color:var(--ink2);white-space:nowrap;background:var(--s2);
border:1px solid var(--grid);border-radius:999px;padding:2px 9px}
.note{font-size:.82rem;color:var(--ink2);margin:6px 0 11px}
.fp{font-family:ui-monospace,SFMono-Regular,monospace;font-size:.74rem;color:var(--mut);
margin-top:2px;word-break:break-all}
input{width:100%;padding:12px 13px;font-size:16px;border:1px solid var(--grid);border-radius:10px;
background:var(--s2);color:var(--ink);font-family:ui-monospace,monospace;transition:border-color .15s,box-shadow .15s}
input:focus{outline:none;border-color:var(--acc);box-shadow:0 0 0 3px color-mix(in srgb,var(--acc) 22%,transparent)}
button{margin-top:10px;width:100%;min-height:44px;padding:11px;font-size:.95rem;font-weight:600;
border:none;border-radius:10px;background:var(--acc);color:var(--acc-ink);cursor:pointer;
transition:filter .15s,transform .05s}
button:hover{filter:brightness(1.06)}button:active{transform:translateY(1px)}
a{color:var(--acc);font-size:.82rem;text-decoration:none}a:hover{text-decoration:underline}
.dot{display:inline-block;width:8px;height:8px;border-radius:50%;flex:none}
.ok{background:var(--good);box-shadow:0 0 0 3px color-mix(in srgb,var(--good) 20%,transparent)}
.no{background:var(--warn);box-shadow:0 0 0 3px color-mix(in srgb,var(--warn) 20%,transparent)}
.msg{padding:12px 14px;border-radius:12px;margin-bottom:18px;font-size:.88rem;line-height:1.5;
background:var(--s);border:1px solid var(--ring);border-inline-start:3px solid var(--acc)}
details.card summary{list-style:none;display:flex;align-items:center;gap:8px}
details.card summary::-webkit-details-marker{display:none}
details.card summary::before{content:"›";color:var(--mut);font-size:1.1rem;transition:transform .15s;line-height:1}
details.card[open] summary::before{transform:rotate(90deg)}
details.card summary b{font-weight:600}
details.card summary .st{margin-inline-start:auto}
.keys{margin-top:11px;display:flex;flex-wrap:wrap;gap:7px}
.chip{display:inline-flex;align-items:center;gap:2px;background:var(--chip);color:var(--chip-ink);
border-radius:999px;padding:4px 6px 4px 11px;font-family:ui-monospace,monospace;font-size:.78rem}
.chip form{display:inline;margin:0}
.chip button{all:unset;cursor:pointer;padding:0 4px;line-height:1;border-radius:999px;color:inherit}
.chip button:hover{filter:brightness(1.15)}
.search{margin:0 0 16px;font-family:inherit}
.grp{background:var(--s);border:1px solid var(--ring);border-radius:14px;margin-bottom:11px;
box-shadow:0 1px 2px rgba(16,24,40,.04);overflow:hidden}
.grp>summary{list-style:none;display:flex;align-items:center;gap:9px;padding:14px 16px;cursor:pointer}
.grp>summary::-webkit-details-marker{display:none}
.grp>summary::after{content:"›";margin-inline-start:auto;color:var(--mut);font-size:1.15rem;
transition:transform .15s}
.grp[open]>summary::after{transform:rotate(90deg)}
.grp>summary .glock{width:26px;height:26px;flex:none;border-radius:8px;background:var(--chip);
color:var(--chip-ink);display:grid;place-items:center;font-size:.8rem}
.grp>summary b{font-weight:600;font-size:.95rem}
.grp>summary .st{margin-inline-start:0}
.grows{border-top:1px solid var(--grid)}
.krow{padding:11px 14px;border-bottom:1px solid var(--grid)}
.krow:last-child{border-bottom:none}
.kmain{display:flex;align-items:center;gap:10px}
.klock{flex:none;color:var(--mut);font-size:.9rem;opacity:.85}
.kcol{flex:1;min-width:0}
.kname{display:block;font-family:ui-monospace,monospace;font-size:.86rem;font-weight:600;
white-space:nowrap;overflow:hidden;text-overflow:ellipsis}
.kfp{display:block;font-family:ui-monospace,monospace;font-size:.7rem;color:var(--mut);
white-space:nowrap;overflow:hidden;text-overflow:ellipsis}
.kacts{flex:none;display:flex;align-items:center;gap:2px}
.icon{all:unset;box-sizing:border-box;cursor:pointer;width:34px;height:34px;border-radius:8px;
display:grid;place-items:center;font-size:1rem;color:var(--ink2);transition:background .15s}
.icon:hover{background:var(--s2)}
.ib{display:inline;margin:0}
.keditor{display:flex;gap:8px;margin-top:10px}
.keditor[hidden]{display:none}
.keditor input{flex:1}.keditor button{margin-top:0;width:auto;padding:0 16px;white-space:nowrap}
.knote{font-size:.76rem;color:var(--mut);margin-top:6px;padding-inline-start:26px}
</style></head><body><div class="w">
<header><div class="lock">🔒</div><div><h1>Secretbox</h1>
<p class="sub">כספת הסודות של אלפרד</p></div></header>
<div class="banner"><span class="i">🛡️</span><span>מגיע רק מה-tailnet שלך. הערך נכתב ישירות לדיסק —
לא עובר בטלגרם ולא נכנס להקשר של הסוכן. חשיפת ערך מתועדת ומדווחת.</span></div>
__MSG__ __CARDS__
</div></body></html>"""


class Handler(BaseHTTPRequestHandler):
    server_version = "secretbox"

    def log_message(self, *a):  # never log request lines — they could carry a body
        pass

    def _identity_ok(self) -> bool:
        """Enforce the tailnet identity when there is one to enforce.

        `tailscale serve` sets Tailscale-User-Login itself and strips any copy
        the client sent, so the header cannot be forged from outside. Its
        ABSENCE therefore means the request did not arrive through the proxy at
        all — i.e. straight to 127.0.0.1, which already requires being on this
        box. Failing open in exactly that case is what keeps a wrong
        EXPECT_LOGIN from locking the owner out of the page they need in order
        to fix it, while still rejecting any other tailnet device."""
        if not EXPECT_LOGIN:
            return True
        got = self.headers.get("Tailscale-User-Login", "")
        if not got:
            return self.client_address[0] in ("127.0.0.1", "::1")
        return token_eq(got, EXPECT_LOGIN)

    def _key_row(self, name, reveal_ref, set_ref, cur, note="", prefix=""):
        """One secret, rendered identically whether it is an alfred-core .env
        slot or a vault entry: 🔒 name + status dot + fingerprint, a 🔓 reveal,
        and an inline ✎ editor to set/rotate the value."""
        dot = "ok" if cur else "no"
        fp = fingerprint(cur) if cur else "—"
        reveal = (f'<form method="POST" action="" class="ib">'
                  f'<input type="hidden" name="reveal" value="{esc(reveal_ref)}">'
                  f'<button class="icon" title="חשוף והעתק">👁</button></form>') if cur else ''
        ph = f"צריך להתחיל ב-{prefix}" if prefix else ("עדכן ערך" if cur else "הדבק ערך חדש")
        editor = (f'<form method="POST" action="" class="keditor" hidden>'
                  f'<input type="hidden" name="setkey" value="{esc(set_ref)}">'
                  f'<input type="password" name="value" placeholder="{esc(ph)}" '
                  f'autocomplete="new-password" spellcheck="false">'
                  f'<button type="submit">{"עדכן" if cur else "שמור"}</button></form>')
        toggle = ("onclick=\"var e=this.closest('.krow').querySelector('.keditor');"
                  "e.hidden=!e.hidden;if(!e.hidden)e.querySelector('input').focus()\"")
        return (f'<div class="krow" data-k="{esc(name.lower())}">'
                f'<div class="kmain"><span class="klock">🔒</span>'
                f'<span class="kcol"><span class="kname">{esc(name)}</span>'
                f'<span class="kfp">{esc(fp)}</span></span>'
                f'<span class="dot {dot}"></span>'
                f'<span class="kacts">{reveal}'
                f'<button class="icon" type="button" title="עדכן/החלף" {toggle}>✎</button>'
                f'</span></div>{editor}'
                + (f'<div class="knote">{esc(note)}</div>' if note else '') + '</div>')

    def _group(self, title, count, rows, is_open=False):
        return (f'<details class="grp"{" open" if is_open else ""}><summary>'
                f'<span class="glock">🔒</span><b>{esc(title)}</b>'
                f'<span class="st">{count} מפתחות</span></summary>'
                f'<div class="grows">{rows}</div></details>')

    def _render(self, msg=""):
        cards = []
        # One live client-side filter across every key in every group.
        cards.append(
            '<input class="search" id="q" placeholder="🔍 חיפוש מפתח…" '
            'oninput="var v=this.value.toLowerCase();'
            "document.querySelectorAll('.grp').forEach(function(g){var any=false;"
            "g.querySelectorAll('.krow').forEach(function(r){"
            "var m=r.dataset.k.indexOf(v)>-1;r.style.display=m?'':'none';if(m)any=true;});"
            "g.style.display=any?'':'none';if(v)g.open=true;});\">")

        # Group 1 — alfred's own .env-backed secrets. Shown by their raw env
        # name (not a friendly label) so a key is a key, no visual "types". Only
        # populated ones appear: an empty slot is not a secret, so it is not
        # listed. reveal_ref is the bare KEY (resolve() finds it in .env);
        # set_ref 'slot:KEY' routes an update through verify + write_secret.
        core_rows = [
            self._key_row(key, f"slot:{key}", f"slot:{key}", cur,
                          slot.get("note", ""), slot.get("prefix", ""))
            for key, slot in all_slots().items()
            if (cur := read_current(slot))]
        if core_rows:
            cards.append(self._group("ליבת אלפרד", len(core_rows), "".join(core_rows), is_open=True))

        # Groups 2..N — every vault project, identical row style.
        vault = load_vault()
        for pname, p in sorted(vault.get("projects", {}).items()):
            entries = {k: e for k, e in p.get("entries", {}).items() if "value" in e}
            if not entries:
                continue
            rows = "".join(
                self._key_row(k, f"{pname}/{k}", f"vault:{pname}/{k}", e["value"])
                for k, e in sorted(entries.items()))
            cards.append(self._group(pname, len(entries), rows))

        # Add a brand-new secret (auto-named, anti-clobber applied by store_secret).
        cards.append("""
<h2 class="sec">סוד חדש</h2>
<div class="card">
<div class="note">תאר במילים שלך מה הסוד. אזהה את השירות לפי חתימת הערך, ואם לא — אבין מהתיאור.
אם השם כבר קיים והערך שונה — לא נדרס: יישמר בשם חדש (אלא אם הישן כבר לא עובד).</div>
<form method="POST" action="" autocomplete="off">
<input type="hidden" name="slot" value="__new__">
<input name="name" placeholder="מה זה? למשל: הטוקן של טוויליו" required maxlength="120" style="margin-bottom:8px">
<input name="project" placeholder="של איזה פרויקט? (לא חובה)" maxlength="60" style="margin-bottom:8px">
<input type="password" name="value" placeholder="הערך" required
 autocomplete="new-password" spellcheck="false">
<button type="submit">שמור בכספת</button></form></div>""")

        pages = load_pages()
        if pages:
            rows = "".join(
                f'<div class="card"><div class="hd"><b>{esc(p["name"])}</b>'
                f'<span class="st">{esc(p.get("note",""))}</span></div>'
                f'<div style="margin-top:6px"><a href="{esc(p["url"])}" target="_blank" rel="noopener">{esc(p["url"])}</a></div>'
                + (f'<div class="fp" style="margin-top:5px">סיסמה: {esc(p["password"])}</div>'
                   if p.get("password") else "")
                + "</div>"
                for p in pages)
            cards.append('<h2 class="sec">הדפים שלנו</h2>' + rows)

        body = PAGE.replace("__CARDS__", "".join(cards)).replace(
            "__MSG__", f'<div class="msg">{msg}</div>' if msg else "")
        data = body.encode()
        self.send_response(200)
        self.send_header("Content-Type", "text/html; charset=utf-8")
        self.send_header("Content-Length", str(len(data)))
        self.send_header("Cache-Control", "no-store")
        self.end_headers()
        self.wfile.write(data)

    def _json(self, code, obj):
        b = json.dumps(obj, ensure_ascii=False).encode()
        self.send_response(code)
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self.send_header("Content-Length", str(len(b)))
        self.end_headers()
        self.wfile.write(b)

    def do_GET(self):
        # /fetch/<peer>/<KEY> — a peer pulling a secret alfred granted it.
        # Bearer-authenticated with the shared peer token, not identity-gated:
        # the caller is another agent's box, not a person in a browser.
        if self.path.startswith("/fetch/"):
            parts = self.path.strip("/").split("/", 2)
            if len(parts) != 3:
                self._json(400, {"error": "use /fetch/<peer>/<KEY>"})
                return
            auth = self.headers.get("Authorization", "").removeprefix("Bearer ").strip()
            code, body = claim_grant(parts[1], parts[2], auth)
            if code == 200:
                try:
                    subprocess.run([str(ALFRED / "opsnotify.sh"),
                                    f"🔐 {parts[1]} משך את {parts[2]} "
                                    f"({fingerprint(body[parts[2].rpartition('/')[2]])})"],
                                   capture_output=True, timeout=25)
                except Exception:
                    pass
            self._json(code, body)
            return
        if self.path.startswith("/bootstrap/"):
            got, why = _bootstrap_claim(self.path.rsplit("/", 1)[-1])
            self._json(200 if got else 403, got or {"error": why})
            return
        if not self._identity_ok():
            self.send_error(403, "not your tailnet identity")
            return
        # /reveal/<name> — show ONE secret's value in the browser to the
        # tailnet-authenticated owner, and audit it. This is the ONLY read path
        # that surfaces a raw value, and it exists ONLY here: there is no CLI
        # --get, because a printed secret lands in an agent transcript. A human
        # on their own tailnet device is a different trust boundary — but every
        # reveal is still logged and announced.
        if self.path.startswith("/reveal/"):
            self._reveal(self.path[len("/reveal/"):])
            return
        self._render()

    def _reveal_identity(self) -> str | None:
        """STRICTER than the rest of the page: reveal surfaces a raw value, so it
        must never use the localhost identity fail-open. Only a human arriving
        through `tailscale serve` (which sets Tailscale-User-Login) may reveal —
        a header-less 127.0.0.1 request, i.e. any agent/script on this box, is
        refused. This is what keeps reveal from becoming the CLI --get the whole
        design forbids. Returns the login on success, else None."""
        who = self.headers.get("Tailscale-User-Login", "")
        if not who or (EXPECT_LOGIN and not token_eq(who, EXPECT_LOGIN)):
            return None
        return who

    def _reveal(self, ref: str):
        from urllib.parse import unquote
        who = self._reveal_identity()
        if not who:
            self.send_error(403, "reveal requires a tailnet identity")
            return
        self._render_reveal(unquote(ref).strip("/"), who)

    def _render_reveal(self, ref: str, who: str):
        # A 'slot:KEY' ref is an alfred-core secret: read it exactly the way the
        # page reads it (read_current handles env-line, file-kind, and non-.env
        # paths) — resolve() only knows the main .env and would miss those.
        if ref.startswith("slot:"):
            slot = all_slots().get(ref[5:])
            val = read_current(slot) if slot else None
            where, ambiguous = "env", []
        else:
            val, where, ambiguous = resolve(ref)
        if ambiguous:
            self._render(f"⚠️ <b>{esc(ref)}</b> קיים בכמה פרויקטים: {esc(', '.join(ambiguous))}. ציין PROJECT/KEY.")
            return
        if not val:
            self._render(f"⚠️ אין סוד בשם <b>{esc(ref)}</b>.")
            return
        # Audit trail: who revealed what, when — fingerprint only in the log.
        try:
            with (ALFRED / "state" / "secret-access.jsonl").open("a", encoding="utf-8") as fh:
                fh.write(json.dumps({
                    "at": datetime.now(timezone.utc).isoformat(timespec="seconds"),
                    "who": who, "ref": ref, "where": where,
                    "fingerprint": fingerprint(val)}) + "\n")
        except Exception:
            pass
        try:
            subprocess.run([str(ALFRED / "opsnotify.sh"),
                            f"🔓 נחשף ערך: {ref}\nע\"י {who} · {fingerprint(val)}"],
                           capture_output=True, timeout=25)
        except Exception:
            pass
        page = ("<!doctype html><meta charset=utf-8>"
                "<meta name=viewport content='width=device-width,initial-scale=1'>"
                "<meta name=robots content=noindex>"
                "<body style='font-family:system-ui;background:#0b0f14;color:#e6edf3;"
                "max-width:32rem;margin:2rem auto;padding:1rem'>"
                f"<p style='color:#8b98a5'>🔓 {esc(ref)} · <span style='font-size:.8rem'>{esc(where)}</span></p>"
                "<textarea id='v' readonly onclick='this.select()' style='width:100%;height:5rem;"
                "background:#111823;color:#e6edf3;border:1px solid #2a3644;border-radius:8px;"
                f"padding:.6rem;font-family:monospace;font-size:.9rem'>{esc(val)}</textarea>"
                "<button id='c' style='margin-top:8px;background:#1d6feb;color:#fff;border:0;"
                "border-radius:8px;padding:.55rem 1rem;cursor:pointer;font-size:.9rem'>📋 העתק</button>"
                "<p style='color:#8b98a5;font-size:.8rem'>ההצגה תועדה ונשלחה התראה. אל תשאיר/י את הדף פתוח.</p>"
                "<a href='/secrets' style='color:#58a6ff'>← חזרה</a>"
                "<script>document.getElementById('c').onclick=async function(){"
                "var t=document.getElementById('v');var s=t.value;"
                "try{await navigator.clipboard.writeText(s);}"
                "catch(e){t.select();document.execCommand('copy');}"
                "this.textContent='✓ הועתק';var b=this;setTimeout(function(){b.textContent='📋 העתק';},1500);"
                "};</script></body>")
        data = page.encode()
        self.send_response(200)
        self.send_header("Content-Type", "text/html; charset=utf-8")
        self.send_header("Content-Length", str(len(data)))
        self.send_header("Cache-Control", "no-store")
        self.send_header("Referrer-Policy", "no-referrer")
        self.end_headers()
        self.wfile.write(data)

    def do_POST(self):
        # Path-agnostic on purpose: this app is mounted under /secrets by
        # `tailscale serve`, so an absolute form action would escape the mount
        # and land on whatever owns "/" — which is how a paste once went to the
        # understudy dashboard instead of here. The form posts to "" (current
        # URL) and this accepts it whatever prefix arrives.
        n = int(self.headers.get("Content-Length", 0) or 0)

        # Machine-to-machine push. Bearer-authenticated rather than identity-
        # gated, because the caller is another box, not a person in a browser.
        if self.path.rstrip("/").endswith("/ingest"):
            want = ""
            for line in (ALFRED / ".env").read_text(encoding="utf-8").splitlines():
                if line.startswith("SECRETBOX_INGEST_TOKEN="):
                    want = line.split("=", 1)[1].strip()
            got = self.headers.get("Authorization", "").removeprefix("Bearer ").strip()
            if not token_eq(got, want):
                self._json(403, {"error": "bad ingest token"})
                return
            if n > 262144:
                self._json(413, {"error": "too large"})
                return
            try:
                payload = json.loads(self.rfile.read(n).decode())
            except Exception:
                self._json(400, {"error": "expected a JSON object of name -> value"})
                return
            res = ingest_secrets(payload if isinstance(payload, dict) else {})
            try:
                subprocess.run([str(ALFRED / "opsnotify.sh"),
                                "🔐 סודות הועברו ל-Secretbox ממכונה אחרת:\n" +
                                "\n".join(f"· {k}" for k in res)], capture_output=True, timeout=25)
            except Exception:
                pass
            self._json(200, {"stored": res})
            return

        if not self._identity_ok():
            self.send_error(403)
            return
        if n > 16384:
            self.send_error(413)
            return
        form = parse_qs(self.rfile.read(n).decode())
        key = (form.get("slot") or [""])[0]
        value = (form.get("value") or [""])[0].strip()

        # Reveal as a same-URL POST (an absolute GET link like /reveal/… escapes
        # the /secrets mount and lands on the wrong app — the exact trap the
        # do_POST comment above describes). Reuses the strict reveal identity.
        reveal_ref = (form.get("reveal") or [""])[0].strip()
        if reveal_ref:
            who = self._reveal_identity()
            if not who:
                self.send_error(403, "reveal requires a tailnet identity")
                return
            self._render_reveal(reveal_ref, who)
            return

        # Inline per-key editor (the ✎ on every row). One field for every key
        # type: 'slot:KEY' goes through the verified .env write; 'vault:P/K' is a
        # deliberate in-place rotation (force replace, old fingerprint retired).
        set_ref = (form.get("setkey") or [""])[0].strip()
        if set_ref:
            if not value:
                self._render("לא התקבל ערך.")
                return
            kind, _, target = set_ref.partition(":")
            if kind == "slot":
                slot = all_slots().get(target)
                if not slot:
                    self._render("סלוט לא ידוע.")
                    return
                pref = slot.get("prefix")
                if pref and not value.startswith(pref):
                    self._render(f"נראה שגוי — {esc(slot['label'])} אמור להתחיל ב-<code>{pref}</code>. לא נשמר.")
                    return
                ok, detail = verify(slot, value)
                if not ok:
                    self._render(f"❌ {esc(slot['label'])}: {esc(detail)}. <b>לא נשמר</b> — הסוד הקודם נשאר בתוקף.")
                    return
                write_secret(slot, value)
                fp = fingerprint(value)
                try:
                    subprocess.run([str(ALFRED / "opsnotify.sh"),
                                    f"🔐 עודכן: {slot['label']}\n{fp}\n{detail}"],
                                   capture_output=True, timeout=25)
                except Exception:
                    pass
                self._render(f"✅ {esc(slot['label'])} נשמר ואומת{esc(restart_after(slot))}.<br><span class='fp'>{fp}</span>")
                return
            if kind == "vault":
                project, _, k = target.rpartition("/")
                if not project or not k:
                    self._render("מפתח כספת לא תקין.")
                    return
                final, action = store_secret(project, k, value, force=True)
                fp = fingerprint(value)
                try:
                    subprocess.run([str(ALFRED / "opsnotify.sh"),
                                    f"🔁 עודכן {project}/{final}\n{fp}"], capture_output=True, timeout=25)
                except Exception:
                    pass
                self._render(f"✅ <b>{esc(k)}</b> עודכן ב-<code>{esc(project)}</code> (הישן נשמר ל-audit).<br><span class='fp'>{fp}</span>")
                return
            self._render("בקשה לא מובנת.")
            return

        if key == "__new__":
            name = (form.get("name") or [""])[0].strip()
            if not name or not value:
                self._render("צריך גם תיאור וגם ערך.")
                return
            ek, how = env_key(name, value)
            if ek in PROTECTED:
                self._render(f"<b>{esc(ek)}</b> הוא מפתח הגדרה ולא סוד — דריסה שלו הייתה שוברת את הגשר. לא נשמר.")
                return
            # A free-form secret goes to the vault, not .env: we know nothing
            # about it, so it must not join the variables every command on this
            # box inherits. The optional project field keeps generic names like
            # DATABASE_URL from colliding across projects.
            project = (form.get("project") or [""])[0].strip() or "misc"
            final_ek, action = store_secret(project, ek, value, note=f"{name} · {how}")
            fp = fingerprint(value)
            verb = {"stored": "נשמר בכספת", "duplicate": "כבר קיים (זהה) — לא נכתב שוב",
                    "renamed": "כבר היה מפתח פעיל בשם הזה — נשמר בשם חדש",
                    "replaced": "הישן נדחה ע\"י השירות — הוחלף (rotation)"}[action]
            try:
                subprocess.run([str(ALFRED / "opsnotify.sh"),
                                f"🔐 {verb}: {name}\n"
                                f"מפתח: {project}/{final_ek}\n{fp}"],
                               capture_output=True, timeout=25)
            except Exception:
                pass
            note_html = ""
            if action == "renamed":
                note_html = (f"<br>המפתח הקיים <code>{esc(project)}/{esc(ek)}</code> עדיין עובד "
                             f"(או שלא ניתן לאמת שמת), אז לא נדרס — החדש נשמר בנפרד.")
            elif action == "replaced":
                note_html = f"<br>המפתח הקודם נכשל בבדיקה חיה, אז הוחלף. הטביעה הישנה נשמרה ל-audit."
            elif action == "duplicate":
                note_html = "<br>הערך זהה למה שכבר שמור — לא נוצר כפל."
            self._render(f"✅ <b>{esc(name)}</b> {esc(verb)} כ-<code>{esc(project)}/{esc(final_ek)}</code> · {esc(how)}"
                         f"{note_html}<br><span class='fp'>{fp}</span>")
            return

        slot = all_slots().get(key)

        if not slot or not value:
            self._render("לא התקבל ערך.")
            return
        pref = slot.get("prefix")
        if pref and not value.startswith(pref):
            self._render(f"נראה שגוי — {esc(slot['label'])} אמור להתחיל ב-<code>{pref}</code>. לא נשמר.")
            return

        ok, detail = verify(slot, value)
        if not ok:
            self._render(f"❌ {esc(slot['label'])}: {esc(detail)}. <b>לא נשמר</b> — הסוד הקודם נשאר בתוקף.")
            return
        write_secret(slot, value)
        fp = fingerprint(value)
        # The agent's only view of this event: name + fingerprint, never the value.
        try:
            subprocess.run([str(ALFRED / "opsnotify.sh"),
                            f"🔐 עודכן: {slot['label']}\n{fp}\n{detail}"],
                           capture_output=True, timeout=25)
        except Exception:
            pass
        note = restart_after(slot)
        self._render(f"✅ {esc(slot['label'])} נשמר ואומת{esc(note)}.<br><span class='fp'>{fp}</span>")


# Secrets that cannot be pasted — they are issued by an interactive device flow
def audit(notify: bool = False) -> int:
    """Print the secret inventory — names and fingerprints. Never values.

    Deliberately says nothing about how old anything is. Age-based prompting
    was removed on request: this reports WHAT IS HELD, and secretcheck.py
    reports WHAT STILL AUTHENTICATES, which is the question that has an
    objective answer. `notify` is retained so the weekly unit's existing
    invocation keeps working; nothing is pushed from here.
    """
    lines = []
    for key, slot in all_slots().items():
        cur = read_current(slot)
        lines.append(f"  ● {slot['label']}: {fingerprint(cur)}" if cur
                     else f"  ○ {slot['label']}: לא מוגדר")

    vault = load_vault()
    vlines, live, unver = [], 0, 0
    for pname, p in sorted(vault.get("projects", {}).items()):
        vals = {k: e for k, e in p.get("entries", {}).items() if "value" in e}
        if not vals:
            continue
        live += sum(1 for e in vals.values() if e.get("status") == "live")
        unver += sum(1 for e in vals.values() if e.get("status") == "unverified")
        vlines.append(f"  · {pname}: {len(vals)} ערכים")

    total = sum(1 for p in vault.get("projects", {}).values()
                for e in p.get("entries", {}).values() if "value" in e)
    print("\n".join(
        ["ריצה — מה שאלפרד עצמו קורא (ערכים לעולם לא מוצגים):", *lines,
         "", f"כספת — {total} ערכים, {live} אומתו כחיים, {unver} לא אומתו:",
         *vlines]))
    return 0


BOOTSTRAP_FILE = ALFRED / ".bootstrap.json"


PEER_DEFAULT = ["BRIDGE_PEER_TOKEN", "SECRETBOX_INGEST_TOKEN"]


def mint_bootstrap(ttl_min: int = 30, keys: list[str] | None = None) -> str:
    """One-time, short-lived capability so another machine can fetch a named
    secret itself instead of a human copying it out of one config into another.

    It is a bearer URL: single use, tailnet-only, and dead after ttl_min. The
    keys are named at mint time and the claim returns nothing else — an agent on
    the far side that needs one credential does not get handed the whole file.
    This is the only sanctioned way a secret crosses machines; the alternative
    people reach for is pasting it into a chat, which is what put the last
    Supabase token through Telegram."""
    import secrets as _s
    tok = _s.token_urlsafe(24)
    fd, tmp = tempfile.mkstemp(dir=str(ALFRED))
    os.write(fd, json.dumps({"token": tok, "expires": time.time() + ttl_min * 60,
                             "keys": keys or PEER_DEFAULT}).encode())
    os.close(fd)
    os.chmod(tmp, 0o600)
    os.replace(tmp, BOOTSTRAP_FILE)
    return tok


BOOTSTRAP_LOG = ALFRED / "bootstrap-claims.jsonl"


def _bootstrap_claim(tok: str):
    """Returns (payload, reason). payload is None unless the claim succeeded.

    The reason is separated out because the original single "invalid, expired,
    or already used" string cost a full round trip with a peer: a link that had
    in fact been claimed successfully looked identical to a bad token, so
    neither side could tell whether the secret had been delivered or not."""
    try:
        d = json.loads(BOOTSTRAP_FILE.read_text(encoding="utf-8"))
    except FileNotFoundError:
        return None, "no bootstrap is pending — it was already claimed, or none was minted"
    except Exception:
        return None, "the pending bootstrap is unreadable"
    if not token_eq(str(d.get("token", "")), tok):
        return None, "that is not the current bootstrap token"
    if time.time() > d.get("expires", 0):
        return None, "this bootstrap expired"
    out = {}
    for name in d.get("keys", PEER_DEFAULT):
        val, _, _ = resolve(name)
        if val:
            out[name.rpartition("/")[2]] = val
    if not out:
        # Don't burn a valid capability on a request that returns nothing —
        # the caller would see a 403 and reasonably conclude the token was
        # wrong, when the real fault is a key name that resolves to nothing.
        return None, ("the bootstrap is valid but none of its keys resolve: "
                      + ", ".join(d.get("keys", [])))
    BOOTSTRAP_FILE.unlink(missing_ok=True)      # single use
    # Record that delivery happened. Without this, "did the peer actually get
    # it?" is unanswerable after the fact, which is precisely the question a
    # failed-looking-but-successful claim leaves behind.
    try:
        with BOOTSTRAP_LOG.open("a", encoding="utf-8") as fh:
            fh.write(json.dumps({
                "at": datetime.now(timezone.utc).isoformat(timespec="seconds"),
                "keys": list(out),
                "fingerprints": {k: fingerprint(v) for k, v in out.items()}}) + "\n")
        BOOTSTRAP_LOG.chmod(0o600)
    except Exception:
        pass
    return out, "delivered"


def live_check(name: str, value: str):
    """Best-effort answer to 'does this secret still authenticate?', reusing the
    validators in secretcheck.py. Returns True (works / valid-but-unpaid),
    False (the service rejected it), or None (no validator, or a multi-key check
    we can't run in isolation). None means 'cannot tell' and must NEVER be read
    as 'dead' — the whole anti-clobber rule turns on not destroying a key we
    merely failed to verify."""
    try:
        import secretcheck
        entry = secretcheck.CHECKS.get(name)
        if not entry:
            return None
        keys, fn, _label = entry
        if keys != [name]:          # needs sibling values we don't have here
            return None
        ok, _detail = fn([value])
        return True if ok in (True, "warn") else False
    except Exception:
        return None


def _next_free_name(base: str, entries: dict) -> str:
    """base, base_2, base_3 … — the first that is not already an entry key."""
    if base not in entries:
        return base
    i = 2
    while f"{base}_{i}" in entries:
        i += 1
    return f"{base}_{i}"


def store_secret(project: str, key: str, value: str, note: str = "",
                 force: bool = False) -> tuple[str, str]:
    """The single write path into the vault, with anti-clobber built in.

    A new secret NEVER silently overwrites an existing one under the same name.
    Returns (final_key, action):
      · stored     — the name was free
      · duplicate  — same name AND identical value already present (no write)
      · renamed    — the name held a DIFFERENT value that still works (or could
                     not be disproven), so the new value is filed under a fresh
                     NAME_n and the old is left untouched
      · replaced   — the name held a value the service now rejects (or force=True),
                     so the new value rotates in and the old's fingerprint is
                     recorded under the project's `retired` list
    """
    vault = load_vault()
    bucket = vault.setdefault("projects", {}).setdefault(
        project, {"description": "", "entries": {}})
    entries = bucket["entries"]
    stamp = datetime.now(timezone.utc).isoformat(timespec="seconds")

    existing = entries.get(key)
    if existing is None:
        final, action = key, "stored"
    elif existing.get("value") == value:
        return key, "duplicate"                       # exact dup — nothing to do
    elif force or live_check(key, existing.get("value", "")) is False:
        final, action = key, "replaced"               # old is dead → rotate
        bucket.setdefault("retired", []).append({
            "key": key, "fingerprint": fingerprint(existing.get("value", "")),
            "retired": stamp,
            "reason": "forced" if force else "superseded (old failed live check)"})
    else:
        final, action = _next_free_name(key, entries), "renamed"  # old lives → keep both

    entry = {"value": value, "status": "unverified", "added": stamp}
    if note:
        entry["note"] = note
    if action == "replaced":
        entry["rotated_from"] = fingerprint(existing.get("value", ""))
    entries[final] = entry
    save_vault(vault)
    return final, action


def vault_put(project: str, key: str, value: str, note: str = "") -> None:
    """Back-compat shim — every caller now goes through the anti-clobber path."""
    store_secret(project, key, value, note)


GRANTS_FILE = ALFRED / "grants.json"


def grant(peer: str, key: str, ttl_min: int = 60) -> dict:
    """Let a named peer pull one named secret, for a while.

    The alternative — minting a bearer URL and messaging it to the peer — puts
    the capability through the sending agent's context, which is the same leak
    the whole system exists to avoid, one level up. Here the peer authenticates
    with the BRIDGE_PEER_TOKEN it already holds, and the only thing that crosses
    the wire from me is the NAME of a key."""
    grants = {}
    try:
        grants = json.loads(GRANTS_FILE.read_text(encoding="utf-8"))
    except Exception:
        pass
    # Drop anything already expired rather than letting the file accumulate a
    # growing list of dead capabilities nobody reviews.
    now = time.time()
    grants = {k: g for k, g in grants.items() if g.get("expires", 0) > now}
    grants[f"{peer}:{key}"] = {"peer": peer, "key": key,
                               "expires": now + ttl_min * 60}
    fd, tmp = tempfile.mkstemp(dir=str(ALFRED))
    os.write(fd, json.dumps(grants, indent=1).encode())
    os.close(fd)
    os.chmod(tmp, 0o600)
    os.replace(tmp, GRANTS_FILE)
    return grants[f"{peer}:{key}"]


def _peer_token() -> str:
    for line in (ALFRED / ".env").read_text(encoding="utf-8").splitlines():
        if line.startswith("BRIDGE_PEER_TOKEN="):
            return line.split("=", 1)[1].strip()
    return ""


def _peer_tokens() -> dict:
    """name -> token, from BRIDGE_PEER_TOKENS."""
    out = {}
    for line in (ALFRED / ".env").read_text(encoding="utf-8").splitlines():
        if line.startswith("BRIDGE_PEER_TOKENS="):
            for part in line.split("=", 1)[1].strip().split(";"):
                if "=" in part:
                    n, _, t = part.partition("=")
                    if n.strip() and t.strip():
                        out[n.strip()] = t.strip()
    return out


def claim_grant(peer: str, key: str, auth: str) -> tuple[int, dict]:
    # A grant is per-peer, so the token must be THAT peer's. Under the old
    # shared token the peer name in the URL was decorative: any holder could
    # claim any peer's grant, which made "granted robin GEMINI_API_KEY" a label
    # rather than a control. The legacy token is still accepted while peers
    # migrate, but it can no longer name a peer it does not own.
    table = _peer_tokens()
    if peer in table:
        if not token_eq(auth, table[peer]):
            if token_eq(auth, _peer_token()):
                return 403, {"error": "legacy shared token cannot claim a "
                                      f"per-peer grant for {peer!r}",
                             "hint": "present your own BRIDGE_PEER_TOKENS entry"}
            return 403, {"error": "bad peer token",
                         "received": ({"len": len(auth),
                                       "fingerprint": fingerprint(auth)} if auth else {}),
                         "hint": f"this grant requires {peer}'s own token"}
        return _deliver(peer, key)
    want = _peer_token()
    if not token_eq(auth, want):
        # Echo back only what the CALLER already knows: the length and
        # fingerprint of the token it just sent. An earlier version also
        # returned the fingerprint of the expected token, which handed a
        # property of the shared peer secret to anyone who could reach this
        # endpoint with no credentials at all — including a caller sending no
        # Authorization header. Never describe the secret to someone who failed
        # to present it.
        if not auth:
            return 403, {"error": "bad peer token",
                         "hint": "no Authorization header — send: Bearer <BRIDGE_PEER_TOKEN>"}
        return 403, {"error": "bad peer token",
                     "received": {"len": len(auth), "fingerprint": fingerprint(auth)},
                     "hint": ("your token is correct but arrived with stray "
                              "whitespace — strip it before sending"
                              if token_eq(auth.strip(), want) else
                              "token does not match the one this box expects")}
    return _deliver(peer, key)


def _deliver(peer: str, key: str) -> tuple[int, dict]:
    """Grant lookup and hand-off, once the caller's identity is settled."""
    try:
        grants = json.loads(GRANTS_FILE.read_text(encoding="utf-8"))
    except Exception:
        grants = {}
    g = grants.get(f"{peer}:{key}")
    if not g:
        return 403, {"error": f"no grant for {peer}:{key} — ask alfred to issue one"}
    if time.time() > g.get("expires", 0):
        return 403, {"error": "grant expired"}
    val, where, ambiguous = resolve(key)
    if ambiguous:
        return 409, {"error": "ambiguous", "candidates": ambiguous}
    if not val:
        return 404, {"error": f"no secret named {key}"}
    return 200, {key.rpartition("/")[2]: val, "source": where}


def ingest_secrets(payload: dict, project: str = "") -> dict:
    """Machine-to-machine secret push.

    Lands in the vault under a project, NOT in .env. The previous version wrote
    every pushed key straight into .env, which is how one push of ~30 projects'
    config ended up in the environment of every command on this box, with
    generic names overwriting each other across projects. Pass {"__project__":
    "name"} in the payload to file the batch; otherwise it goes to "ingested".

    Only fingerprints come back, so nothing readable crosses into any agent's log.
    """
    out = {}
    project = project or str(payload.get("__project__") or "").strip() or "ingested"
    for raw_name, value in payload.items():
        if raw_name == "__project__" or not isinstance(value, str) or not value.strip():
            continue
        value = value.strip()
        ek = raw_name if re.fullmatch(r"[A-Z][A-Z0-9_]*", raw_name) else (key_from_value(value) or env_key(raw_name)[0])
        if ek in PROTECTED:
            out[raw_name] = "refused: protected config key"
            continue
        # Anti-clobber: a machine push must not overwrite a working key any more
        # than a hand-entered one does. store_secret renames around a live key
        # and only replaces one the service already rejects.
        final, action = store_secret(project, ek, value)
        out[raw_name] = f"{project}/{final} {fingerprint(value)} ({action})"
    return out


def resolve(name: str) -> tuple[str | None, str, list[str]]:
    """Find a secret by KEY, or PROJECT/KEY for a vault entry.

    Runtime .env first, then the vault. Returns (value, where, ambiguous)."""
    key = name.rpartition("/")[2]
    if "/" not in name:
        for line in (ALFRED / ".env").read_text(encoding="utf-8").splitlines():
            if line.startswith(key + "=") and not line.lstrip().startswith("#"):
                v = line.split("=", 1)[1].strip().strip("\"'")
                if v:
                    return v, "env", []
    return vault_find(name)


def _resolve_or_explain(name: str) -> str | None:
    val, where, ambiguous = resolve(name)
    if ambiguous:
        print("secretbox: " + name + " exists under several projects — "
              "name one:\n  " + "\n  ".join(ambiguous), flush=True)
        return None
    if not val:
        print(f"secretbox: no secret named {name}", flush=True)
        return None
    print(f"secretbox: {name} ← {where} ({fingerprint(val)})", flush=True)
    return val


def pipe_to(key: str, argv: list[str]) -> int:
    """Feed a secret to another command's stdin without ever printing it.

    `--get` deliberately does not exist: printing a secret puts it in the
    caller's transcript, which is the exact failure this whole thing was built
    to avoid. Instead:

        secretbox.py --pipe SENTRY_AUTH_TOKEN -- vercel env add SENTRY_AUTH_TOKEN production

    The value goes down a pipe into the child and nowhere else.
    """
    if not argv:
        print("secretbox: --pipe needs a command after --", flush=True)
        return 2
    val = _resolve_or_explain(key)
    if not val:
        return 2
    p = subprocess.run(argv, input=val, text=True)
    print(f"secretbox: piped {key} → {argv[0]} (exit {p.returncode})")
    return p.returncode


def run_with(names: list[str], argv: list[str]) -> int:
    """Run a command with the named secrets in ITS environment only.

    Most CLIs want a credential in an env var, not on stdin, which left --pipe
    unusable for the common case and tempted callers to echo the value instead.
    The child gets it; this process's environment is untouched, the value never
    reaches a command line where `ps` could read it, and nothing is printed.

        secretbox.py --run VERCEL_TOKEN -- vercel deploy
        secretbox.py --run 'skyhawk/DATABASE_URL=DATABASE_URL' -- psql

    Each name may be VAULT_NAME=ENV_NAME when the child expects a different
    variable than the one the secret is filed under.
    """
    if not argv:
        print("secretbox: --run needs a command after --", flush=True)
        return 2
    extra = {}
    for spec in names:
        src, _, dst = spec.partition("=")
        val = _resolve_or_explain(src)
        if not val:
            return 2
        extra[dst or src.rpartition("/")[2]] = val
    p = subprocess.run(argv, env={**os.environ, **extra})
    print(f"secretbox: ran {argv[0]} with {', '.join(extra)} (exit {p.returncode})")
    return p.returncode


def rotate(ref: str, force: bool = False) -> int:
    """Deliberately replace a secret's value with a new one read from STDIN
    (never argv, so it stays out of `ps`). Safety: if the KEY has a validator,
    the NEW value is verified BEFORE the old is retired — you never rotate to a
    key the service already rejects. --force skips that guard. The old value's
    fingerprint is kept in the project's `retired` list either way.

        printf %s "$NEWVAL" | secretbox.py --rotate PROJECT/KEY
    """
    project, _, key = ref.rpartition("/")
    if not project or not key:
        print("secretbox: --rotate needs PROJECT/KEY", flush=True)
        return 2
    new = sys.stdin.read().strip()
    if not new:
        print("secretbox: no new value on stdin", flush=True)
        return 2
    live = live_check(key, new)
    if live is False and not force:
        print(f"secretbox: REFUSING — the new value for {key} fails its live check "
              f"(the service rejects it). Re-run with --force to rotate anyway.", flush=True)
        return 1
    final, action = store_secret(project, key, new, force=True)
    verdict = {True: "verified live", None: "no validator (unverified)",
               False: "FORCED past a failed check"}[live]
    print(f"secretbox: rotated {project}/{final} ({action}) — new value {verdict}; "
          f"{fingerprint(new)}", flush=True)
    try:
        subprocess.run([str(ALFRED / "opsnotify.sh"),
                        f"🔁 מפתח סובב: {project}/{final}\n{verdict} · {fingerprint(new)}"],
                       capture_output=True, timeout=25)
    except Exception:
        pass
    return 0


def _canon_project(name: str) -> str:
    """Fold punctuation/case so 'Yishaik.com' and 'yishaik-com' group together."""
    return re.sub(r"[.\-_ ]+", "-", name.strip().lower()).strip("-")


def tidy(apply: bool = False) -> int:
    """Report (and with apply=True, fix) vault untidiness WITHOUT ever losing a
    distinct secret:
      · project-name variants (dot/dash/case) are merged into one canonical name;
        entries move via store_secret, so a name clash with a DIFFERENT value is
        kept as NAME_2 rather than overwritten
      · identical VALUES held under several names are reported (never auto-deleted
        — which of the duplicates other code references is not ours to guess)
      · the same key name in several projects with DIFFERENT values is reported as
        correct-by-design (kept, distinguished by project)
    """
    from collections import defaultdict
    vault = load_vault()
    projs = vault.get("projects", {})

    groups = defaultdict(list)
    for n in projs:
        groups[_canon_project(n)].append(n)

    moves = []
    for canon, variants in groups.items():
        if len(variants) < 2:
            continue
        # Canonical target: prefer an existing variant already in dash-lower form.
        target = next((v for v in variants if v == canon), None) or sorted(variants, key=len)[0]
        for src in variants:
            if src == target:
                continue
            for k, e in list(projs.get(src, {}).get("entries", {}).items()):
                if "value" not in e:
                    continue
                if apply:
                    final, action = store_secret(target, k, e["value"], e.get("note", ""))
                    moves.append(f"{src}/{k} → {target}/{final} ({action})")
                else:
                    moves.append(f"{src}/{k} → {target}/{k}")

    if apply:
        # store_secret copied every value-entry into the target; now retire the
        # variant source projects wholesale (their secrets are safely in target,
        # whether as an exact duplicate, a renamed NAME_2, or a rotation).
        vault = load_vault()
        for canon, variants in groups.items():
            if len(variants) < 2:
                continue
            target = next((v for v in variants if v == canon), None) or sorted(variants, key=len)[0]
            for src in variants:
                if src != target:
                    vault.get("projects", {}).pop(src, None)
        save_vault(vault)

    print(("APPLIED" if apply else "DRY RUN") + " — project-variant merges:")
    print("  " + "\n  ".join(moves) if moves else "  (no project-name variants)")

    # value-duplicate + same-key-different-value reports (informational)
    byval, bykey = defaultdict(list), defaultdict(list)
    for pn, p in load_vault().get("projects", {}).items():
        for k, e in p.get("entries", {}).items():
            if "value" in e:
                byval[e["value"]].append(f"{pn}/{k}")
                bykey[k].append((pn, e["value"]))
    dups = {fingerprint(v): locs for v, locs in byval.items() if len(locs) > 1}
    print("\nidentical values under multiple names (kept — dedupe by hand if intended):")
    print("  " + "\n  ".join(f"{fp} → {locs}" for fp, locs in dups.items()) if dups else "  (none)")
    print("\nsame key in multiple projects with DIFFERENT values (correct — kept both):")
    diffs = [k for k, l in bykey.items() if len(l) > 1 and len({fingerprint(v) for _, v in l}) > 1]
    print("  " + "\n  ".join(diffs) if diffs else "  (none)")
    return 0


if __name__ == "__main__":
    import sys
    if "--tidy" in sys.argv:
        raise SystemExit(tidy(apply="--apply" in sys.argv))
    if "--rotate" in sys.argv:
        i = sys.argv.index("--rotate")
        ref = sys.argv[i + 1] if len(sys.argv) > i + 1 else ""
        raise SystemExit(rotate(ref, force="--force" in sys.argv))
    if "--grant" in sys.argv:
        i = sys.argv.index("--grant")
        peer, key = sys.argv[i + 1], sys.argv[i + 2]
        ttl = int(sys.argv[sys.argv.index("--ttl") + 1]) if "--ttl" in sys.argv else 60
        grant(peer, key, ttl)
        print(f"granted {peer} → {key} for {ttl} min. Tell the peer to run:\n"
              f'  curl -sf -H "Authorization: Bearer $BRIDGE_PEER_TOKEN" \\\n'
              f"       https://alfred.tailbb9b2e.ts.net/secrets/fetch/{peer}/{key}")
        raise SystemExit(0)
    if "--mint-bootstrap" in sys.argv:
        i = sys.argv.index("--mint-bootstrap")
        rest = [a for a in sys.argv[i + 1:] if not a.startswith("-")]
        keys = rest[0].split(",") if rest else None
        ttl = int(sys.argv[sys.argv.index("--ttl") + 1]) if "--ttl" in sys.argv else 30
        tok = mint_bootstrap(ttl, keys)
        print(f"https://alfred.tailbb9b2e.ts.net/secrets/bootstrap/{tok}")
        print(f"one use · {ttl} min · {', '.join(keys or PEER_DEFAULT)}")
        raise SystemExit(0)
    if "--pipe" in sys.argv:
        i = sys.argv.index("--pipe")
        k = sys.argv[i + 1] if len(sys.argv) > i + 1 else ""
        rest = sys.argv[sys.argv.index("--") + 1:] if "--" in sys.argv else []
        raise SystemExit(pipe_to(k, rest))
    if "--run" in sys.argv:
        i = sys.argv.index("--run")
        end = sys.argv.index("--") if "--" in sys.argv else len(sys.argv)
        raise SystemExit(run_with(sys.argv[i + 1:end], sys.argv[end + 1:]))
    if "--list" in sys.argv:
        for kk, s in all_slots().items():
            if s.get("kind") == "env":
                print(s["key"], "— set" if read_current(s) else "— empty")
        for pname, p in sorted(load_vault().get("projects", {}).items()):
            for k in sorted(kk for kk, e in p.get("entries", {}).items() if "value" in e):
                print(f"{pname}/{k}")
        raise SystemExit(0)
    if "--audit" in sys.argv:
        raise SystemExit(0 if audit(notify="--notify" in sys.argv) == 0 else 0)
    print(f"secretbox on http://{HOST}:{PORT}")
    ThreadingHTTPServer((HOST, PORT), Handler).serve_forever()
