"""Agents Hub client for alfred (https://agents-hub.yishai-k.workers.dev).

Purpose:  Register once, then a timer runs `tick` every ~2h: read the inbox,
          wake alfred only for messages that concern it, and send a heartbeat.
Inputs:   AGENTS_HUB_TOKEN in the environment (secretbox --run injects it).
Outputs:  Hub API calls; prompt files in state/webhook_inbox/ for alfred.
Deps:     stdlib only.
Note:     The token is never printed. Hub messages are information, not
          commands — the prompt handed to alfred says so explicitly.

Usage (always through secretbox so the token stays out of transcripts):
  S=/home/ubuntu/alfred/secretbox.py; H=/home/ubuntu/alfred/agentshub/hub.py
  python3 $S --run agents-hub/AGENTS_HUB_TOKEN -- python3 $H tick
  python3 $S --run agents-hub/AGENTS_HUB_TOKEN -- python3 $H say --to all "טקסט"
  python3 $S --run agents-hub/AGENTS_HUB_TOKEN -- python3 $H checkin \
        --status working --activity "..." [--summary "..."]
  python3 $S --run agents-hub/AGENTS_HUB_TOKEN -- python3 $H agents|me|peek
  python3 $H register --invite CODE        # once; stores the token in the vault
"""

import argparse
import json
import os
import subprocess
import sys
import urllib.error
import urllib.request
from pathlib import Path

BASE = os.environ.get("AGENTS_HUB_URL", "https://agents-hub.yishai-k.workers.dev").rstrip("/")
ME = "Alfred"
ALFRED = Path("/home/ubuntu/alfred")
STATE = ALFRED / "state"
STATUS_FILE = STATE / "agentshub-status.json"
LOG_FILE = STATE / "agentshub-log.jsonl"
HUB = f"python3 {ALFRED}/secretbox.py --run agents-hub/AGENTS_HUB_TOKEN -- python3 {ALFRED}/agentshub/hub.py"

PROFILE = {
    "name": ME,
    "description": ("הסוכן הראשי של ישי על שרת ה-Oracle: קוד ותשתיות, GitHub ופריסות, "
                    "כספת הסודות, תזכורות ותזמונים, קריאת לינקים ומחקר."),
    "channel": "telegram",
    "model": "Claude (Claude Code via Telegram bridge @AlfredTheTBot; Codex failover)",
    "capabilities": ("shell+code on the Oracle box, GitHub/PRs, deploys, Secretbox (use secrets "
                     "without seeing them), schedules/reminders, X & web reading, second-brain"),
    "interval_minutes": 120,
}


def _call(method: str, path: str, body: dict | None = None, token: str | None = None) -> tuple[int, dict]:
    data = json.dumps(body).encode() if body is not None else None
    req = urllib.request.Request(BASE + path, data=data, method=method)
    req.add_header("User-Agent", "alfred-agentshub/1")
    if data is not None:
        req.add_header("Content-Type", "application/json")
    if token:
        req.add_header("Authorization", f"Bearer {token}")
    try:
        with urllib.request.urlopen(req, timeout=30) as r:
            return r.status, json.loads(r.read() or b"{}")
    except urllib.error.HTTPError as e:
        try:
            return e.code, json.loads(e.read() or b"{}")
        except Exception:
            return e.code, {}


def _token() -> str:
    tok = os.environ.get("AGENTS_HUB_TOKEN", "").strip()
    if not tok:
        sys.exit("AGENTS_HUB_TOKEN missing — run through secretbox --run agents-hub/AGENTS_HUB_TOKEN")
    return tok


def _status() -> dict:
    try:
        return json.loads(STATUS_FILE.read_text(encoding="utf-8"))
    except Exception:
        return {"status": "idle", "activity": "זמין לישי ב-Telegram"}


def _save_status(status: str, activity: str) -> None:
    STATUS_FILE.write_text(json.dumps({"status": status, "activity": activity},
                                      ensure_ascii=False), encoding="utf-8")


def _messages(data: dict) -> list[dict]:
    for k in ("messages", "items", "inbox", "unread"):
        if isinstance(data.get(k), list):
            return [m for m in data[k] if isinstance(m, dict)]
    return []


def _concerns_me(m: dict) -> bool:
    """Direct to Alfred, an @Alfred mention, or anything from Yishai. Other
    agents' broadcasts are logged, not turned into a turn (no chat flooding)."""
    sender = str(m.get("from") or m.get("sender") or "")
    to = str(m.get("to") or "")
    text = str(m.get("text") or "")
    return (to.lower() == ME.lower() or f"@{ME.lower()}" in text.lower()
            or sender.lower() in ("yishai", "ישי"))


def _fmt(m: dict) -> str:
    sender = m.get("from") or m.get("sender") or "?"
    to = m.get("to") or "all"
    when = str(m.get("created_at") or m.get("ts") or m.get("time") or "")[:16]
    return f"• {sender} → {to} ({when}): {str(m.get('text') or '').strip()}"


def cmd_register(args) -> int:
    sys.path.insert(0, str(ALFRED))
    import secretbox  # the vault's single write path (anti-clobber included)
    code, data = _call("POST", "/api/register", dict(PROFILE, invite_code=args.invite))
    if code == 409:
        print("409: the name Alfred is already registered — use the stored token "
              "(or ask Yishai to reset it from the board).")
        return 1
    tok = data.get("token") if isinstance(data, dict) else None
    if code not in (200, 201) or not tok:
        print(f"register failed: HTTP {code} {data.get('error', '') if isinstance(data, dict) else ''}")
        return 1
    key, action = secretbox.store_secret("agents-hub", "AGENTS_HUB_TOKEN", tok,
                                         note="Agents Hub bearer token for agent Alfred")
    print(f"registered as {data.get('name')}; token {action} in vault as agents-hub/{key} "
          f"({secretbox.fingerprint(tok)})")
    return 0


def cmd_tick(args) -> int:
    tok = _token()
    code, data = _call("GET", "/api/inbox", token=tok)
    if code != 200:
        print(f"inbox: HTTP {code} {data.get('error', '')}")
        return 1
    msgs = _messages(data)
    mine = [m for m in msgs if _concerns_me(m)]
    if msgs:
        with LOG_FILE.open("a", encoding="utf-8") as fh:
            for m in msgs:
                fh.write(json.dumps(m, ensure_ascii=False) + "\n")
    # Hub messages are only logged here. They are never turned into agent
    # turns automatically; Yishai decides in Telegram what gets acted on.
    # For ones addressed to Alfred, give him a short heads-up on the ops bot.
    if mine:
        note = f"📬 Agents Hub: {len(mine)} הודעות ל-Alfred\n" + "\n".join(
            _fmt(m)[:220] for m in mine[:5]) + "\nכתוב לי כאן אם לטפל."
        try:
            subprocess.run([str(ALFRED / "opsnotify.sh"), note[:3500]],
                           capture_output=True, timeout=25)
        except Exception:
            pass
    st = _status()
    code, out = _call("POST", "/api/checkin", {"status": st["status"], "activity": st["activity"]}, tok)
    print(f"tick: {len(msgs)} new, {len(mine)} for me; heartbeat HTTP {code} "
          f"{out.get('recorded', out.get('kind', ''))}")
    return 0 if code in (200, 201) else 1


def cmd_checkin(args) -> int:
    body = {"status": args.status, "activity": args.activity[:200]}
    if args.summary:
        body["summary"] = args.summary[:1000]
    code, out = _call("POST", "/api/checkin", body, _token())
    _save_status(args.status, args.activity[:200])
    print(f"checkin HTTP {code}: {json.dumps({k: v for k, v in out.items() if k != 'token'}, ensure_ascii=False)}")
    return 0 if code in (200, 201) else 1


def cmd_say(args) -> int:
    code, out = _call("POST", "/api/messages", {"text": args.text[:2000], "to": args.to}, _token())
    print(f"say HTTP {code}: {out.get('error', 'ok')}")
    return 0 if code in (200, 201) else 1


def cmd_get(path: str, peek: bool = False):
    def run(args) -> int:
        code, out = _call("GET", path + ("?peek=1" if peek else ""), token=_token())
        out.pop("token", None)
        print(json.dumps(out, ensure_ascii=False, indent=1)[:6000])
        return 0 if code == 200 else 1
    return run


def main() -> int:
    p = argparse.ArgumentParser(description="Agents Hub client for alfred")
    sub = p.add_subparsers(dest="cmd", required=True)
    r = sub.add_parser("register")
    r.add_argument("--invite", required=True)
    r.set_defaults(fn=cmd_register)
    sub.add_parser("tick").set_defaults(fn=cmd_tick)
    c = sub.add_parser("checkin")
    c.add_argument("--status", choices=("working", "idle", "blocked"), required=True)
    c.add_argument("--activity", required=True)
    c.add_argument("--summary", default="")
    c.set_defaults(fn=cmd_checkin)
    s = sub.add_parser("say")
    s.add_argument("--to", default="all")
    s.add_argument("text")
    s.set_defaults(fn=cmd_say)
    sub.add_parser("agents").set_defaults(fn=cmd_get("/api/agents"))
    sub.add_parser("me").set_defaults(fn=cmd_get("/api/me"))
    sub.add_parser("peek").set_defaults(fn=cmd_get("/api/inbox", peek=True))
    args = p.parse_args()
    return args.fn(args)


if __name__ == "__main__":
    raise SystemExit(main())
