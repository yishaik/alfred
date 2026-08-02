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
        "verify": ["bash", "-lc", "supabase login --token \"$SECRET\" >/dev/null 2>&1 && supabase projects list >/dev/null 2>&1"],
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
        "note": "אחרי שמירה צריך להפעיל מחדש את הגשר",
    },
    "opsbot": {
        "label": "טוקן הבוט של AlfredOps",
        "kind": "env",
        "path": ALFRED / ".env",
        "key": "OPS_BOT_TOKEN",
        "prefix": None,
        "verify": ["bash", "-lc", "curl -sSf https://api.telegram.org/bot$SECRET/getMe >/dev/null"],
        "note": "התראות תפעול בלבד",
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
    try:
        return json.loads(VAULT_FILE.read_text(encoding="utf-8"))
    except Exception:
        return {"version": 1, "projects": {}}


def save_vault(d: dict) -> None:
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
    try:
        return json.loads(PAGES_FILE.read_text(encoding="utf-8"))
    except Exception:
        return []


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


def verify(slot: dict, value: str) -> tuple[bool, str]:
    cmd = slot.get("verify")
    if not cmd:
        return True, "אין בדיקה מוגדרת — נשמר בלי אימות"
    env = {**os.environ, "SECRET": value,
           "PATH": os.environ.get("PATH", "") + ":/home/ubuntu/.local/bin"}
    try:
        r = subprocess.run(cmd, env=env, capture_output=True, timeout=45)
        return (r.returncode == 0,
                "אומת מול השירות" if r.returncode == 0 else "השירות דחה את הסוד")
    except Exception as e:
        return False, f"הבדיקה נכשלה: {type(e).__name__}"


PAGE = """<!DOCTYPE html><html lang="he" dir="rtl"><head><meta charset="utf-8">
<meta name="viewport" content="width=device-width,initial-scale=1"><title>Secretbox</title><style>
:root{color-scheme:light dark;--s:#fcfcfb;--p:#f9f9f7;--ink:#0b0b0b;--ink2:#52514e;--mut:#898781;
--grid:#e1e0d9;--ring:rgba(11,11,11,.10);--good:#0ca30c;--warn:#fab219;--acc:#2a78d6}
@media(prefers-color-scheme:dark){:root{--s:#1a1a19;--p:#0d0d0d;--ink:#fff;--ink2:#c3c2b7;
--grid:#2c2c2a;--ring:rgba(255,255,255,.10);--acc:#3987e5}}
*{box-sizing:border-box}body{margin:0;padding:20px 14px 60px;background:var(--p);color:var(--ink);
font-family:system-ui,-apple-system,sans-serif;line-height:1.55}.w{max-width:620px;margin:0 auto}
h1{font-size:1.4rem;margin:0 0 4px}.meta{color:var(--ink2);font-size:.85rem;margin:0 0 20px}
.card{background:var(--s);border:1px solid var(--ring);border-radius:12px;padding:14px 16px;margin-bottom:11px}
.hd{display:flex;justify-content:space-between;align-items:baseline;gap:8px}
.hd b{font-size:1rem}.st{font-size:.75rem;color:var(--ink2);white-space:nowrap}
.note{font-size:.82rem;color:var(--ink2);margin:3px 0 9px}
.fp{font-family:ui-monospace,monospace;font-size:.76rem;color:var(--mut)}
input{width:100%;padding:11px 12px;font-size:16px;border:1px solid var(--ring);border-radius:9px;
background:var(--p);color:var(--ink);font-family:ui-monospace,monospace}
button{margin-top:8px;width:100%;padding:11px;font-size:.95rem;font-weight:600;border:none;
border-radius:9px;background:var(--acc);color:#fff}
a{color:var(--acc);font-size:.82rem}
.dot{display:inline-block;width:9px;height:9px;border-radius:50%;margin-inline-end:5px}
.ok{background:var(--good)}.no{background:var(--warn)}
.msg{padding:11px 14px;border-radius:10px;margin-bottom:16px;font-size:.9rem;
background:var(--s);border:1px solid var(--ring);border-inline-start:3px solid var(--acc)}
</style></head><body><div class="w">
<h1>Secretbox</h1>
<p class="meta">מגיע רק מה-tailnet שלך. הערך נכתב ישירות לדיסק — לא עובר בטלגרם ולא נכנס להקשר של הסוכן.</p>
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

    def _render(self, msg=""):
        cards = []
        for key, slot in all_slots().items():
            cur = read_current(slot)
            dot = "ok" if cur else "no"
            state = "קיים" if cur else "לא מוגדר"
            fp = f'<div class="fp">{fingerprint(cur)}</div>' if cur else ""
            cards.append(f"""<div class="card">
<div class="hd"><b><span class="dot {dot}"></span>{esc(slot["label"])}</b><span class="st">{state}</span></div>
<div class="note">{esc(slot["note"])}</div>{fp}
<form method="POST" action="" autocomplete="off">
<input type="hidden" name="slot" value="{key}">
<input type="password" name="value" placeholder="הדבק כאן" autocomplete="new-password" spellcheck="false">
<button type="submit">שמור ואמת</button></form>
</div>""")
        # Free-form slot: a name and a value. No verification is possible for a
        # secret we know nothing about, so it is stored as given — the name is
        # normalised to an env key and protected config keys are refused.
        cards.append("""
<h2 style="font-size:1.05rem;margin:26px 0 10px;padding-top:16px;
border-top:1px solid var(--grid)">סוד חדש</h2>
<div class="card">
<div class="note">תאר במילים שלך מה הסוד, בעברית או באנגלית. אזהה את השירות לפי חתימת הערך, ואם לא — אבין מהתיאור. השם הטכני ייקבע אוטומטית
ויישמר ב-.env של אלפרד. אין אימות מול שירות — סוד שלא מוכר לנו נשמר כפי שהוא.</div>
<form method="POST" action="" autocomplete="off">
<input type="hidden" name="slot" value="__new__">
<input name="name" placeholder="מה זה? למשל: הטוקן של טוויליו" required maxlength="120" style="margin-bottom:8px">
<input name="project" placeholder="של איזה פרויקט? (לא חובה)" maxlength="60" style="margin-bottom:8px">
<input type="password" name="value" placeholder="הערך" required
 autocomplete="new-password" spellcheck="false">
<button type="submit">שמור בכספת</button></form></div>""")

        # The vault is shown as a per-project roll-up, never row-by-row: when
        # ~230 entries each got their own card this page stopped being usable
        # on a phone, which is the only device it is ever opened from.
        vault = load_vault()
        projs = {n: p for n, p in sorted(vault.get("projects", {}).items())
                 if any("value" in e for e in p.get("entries", {}).values())}
        if projs:
            total = sum(sum(1 for e in p["entries"].values() if "value" in e)
                        for p in projs.values())
            rows = []
            for pname, p in projs.items():
                keys = sorted(k for k, e in p["entries"].items() if "value" in e)
                live = sum(1 for e in p["entries"].values() if e.get("status") == "live")
                rows.append(
                    f'<details class="card"><summary style="cursor:pointer">'
                    f'<b>{esc(pname)}</b> <span class="st">· {len(keys)} ערכים'
                    + (f" · {live} אומתו" if live else "") + '</span></summary>'
                    f'<div class="fp" style="margin-top:8px;line-height:1.9">'
                    + ", ".join(esc(k) for k in keys) + "</div></details>")
            cards.append(
                '<h2 style="font-size:1.05rem;margin:26px 0 10px;padding-top:16px;'
                'border-top:1px solid var(--grid)">הכספת</h2>'
                f'<p class="note" style="margin:-4px 0 10px">{total} ערכים שמורים '
                f'ולא נטענים לסביבה של אף תהליך. שליפה: '
                f'<code>secretbox.py --run KEY -- פקודה</code></p>' + "".join(rows))

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
            cards.append('<h2 style="font-size:1.05rem;margin:26px 0 10px;'
                         'padding-top:16px;border-top:1px solid var(--grid)">הדפים שלנו</h2>' + rows)

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
        self._render()

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
            vault_put(project, ek, value, note=f"{name} · {how}")
            fp = fingerprint(value)
            try:
                subprocess.run([str(ALFRED / "opsnotify.sh"),
                                f"🔐 סוד חדש נשמר בכספת: {name}\n"
                                f"מפתח: {project}/{ek}\n{fp}"],
                               capture_output=True, timeout=25)
            except Exception:
                pass
            self._render(f"✅ <b>{esc(name)}</b> נשמר בכספת כ-<code>{esc(project)}/{esc(ek)}</code> · {esc(how)}<br>"
                         f"<span class='fp'>{fp}</span>")
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
        self._render(f"✅ {esc(slot['label'])} נשמר ואומת.<br><span class='fp'>{fp}</span>")


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


def vault_put(project: str, key: str, value: str, note: str = "") -> None:
    vault = load_vault()
    p = vault.setdefault("projects", {}).setdefault(
        project, {"description": "", "entries": {}})
    p["entries"][key] = {"value": value, "status": "unverified",
                         "added": datetime.now(timezone.utc).isoformat(timespec="seconds"),
                         **({"note": note} if note else {})}
    save_vault(vault)


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
    vault = load_vault()
    bucket = vault.setdefault("projects", {}).setdefault(
        project, {"description": "הועבר ממכונה אחרת", "entries": {}})
    stamp = datetime.now(timezone.utc).isoformat(timespec="seconds")
    for raw_name, value in payload.items():
        if raw_name == "__project__" or not isinstance(value, str) or not value.strip():
            continue
        value = value.strip()
        ek = raw_name if re.fullmatch(r"[A-Z][A-Z0-9_]*", raw_name) else (key_from_value(value) or env_key(raw_name)[0])
        if ek in PROTECTED:
            out[raw_name] = "refused: protected config key"
            continue
        bucket["entries"][ek] = {"value": value, "status": "unverified", "added": stamp}
        out[raw_name] = f"{project}/{ek} {fingerprint(value)}"
    save_vault(vault)
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


if __name__ == "__main__":
    import sys
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
