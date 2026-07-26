#!/usr/bin/env python3
"""bloopy-network Railway watcher (sidecar).

Cheap, no-LLM poller: watches yishaik/bloopy-network for open issues carrying
the `railway` label and, only when there's REAL new activity from the code
agent (a new issue, or a new comment that isn't one of Alfred's own replies),
drops a prompt into the bridge scheduler's webhook inbox. The bridge injects it
into the `main` session so Alfred wakes up ONLY on change — no idle spam.

Run:  python railway_watch.py           # loop (default 25s)
      python railway_watch.py --once     # single check (for Task Scheduler)
"""
import json
import subprocess
import sys
import time
import uuid
from pathlib import Path

REPO = "yishaik/bloopy-network"
IGNORE_ISSUES = {9}                 # the protocol issue documents the channel
POLL_SECONDS = 25

ROOT = Path(__file__).resolve().parent
STATE_DIR = ROOT / "state"
INBOX = STATE_DIR / "webhook_inbox"
STATE_FILE = STATE_DIR / "railway_watch_state.json"


def _webhook_secret() -> str:
    """Read BRIDGE_WEBHOOK_SECRET straight from .env — the bridge rejects inbox
    drops without it, so this sidecar must include it."""
    try:
        for line in (ROOT / ".env").read_text(encoding="utf-8").splitlines():
            if line.startswith("BRIDGE_WEBHOOK_SECRET="):
                return line.split("=", 1)[1].strip()
    except OSError:
        pass
    return ""


def _gh(args):
    r = subprocess.run(["gh", *args], capture_output=True, text=True, timeout=45)
    if r.returncode != 0:
        raise RuntimeError(r.stderr.strip()[:200])
    return r.stdout


def _my_login():
    try:
        return _gh(["api", "user", "--jq", ".login"]).strip() or "yishaik"
    except Exception:
        return "yishaik"


def _load_state():
    try:
        return json.loads(STATE_FILE.read_text(encoding="utf-8"))
    except Exception:
        return {}


def _save_state(state):
    STATE_DIR.mkdir(exist_ok=True)
    tmp = STATE_FILE.with_suffix(".tmp")
    tmp.write_text(json.dumps(state), encoding="utf-8")
    tmp.replace(STATE_FILE)


def _inject(text):
    INBOX.mkdir(parents=True, exist_ok=True)
    payload = {"agent": "main", "text": text}
    secret = _webhook_secret()
    if secret:
        payload["secret"] = secret
    (INBOX / f"{uuid.uuid4().hex}.json").write_text(
        json.dumps(payload), encoding="utf-8")


def check_once(my_login, state):
    """Return updated state; inject a prompt for each genuinely-new item."""
    try:
        raw = _gh(["issue", "list", "--repo", REPO, "--label", "railway",
                   "--state", "open", "--json", "number", "--jq", ".[].number"])
    except Exception as e:
        print(f"[railway_watch] list failed: {e}", file=sys.stderr)
        return state
    open_nums = [int(n) for n in raw.split() if n.strip().isdigit() and int(n) not in IGNORE_ISSUES]

    fresh = {}
    for n in open_nums:
        try:
            data = json.loads(_gh(["issue", "view", str(n), "--repo", REPO,
                                   "--json", "number,title,updatedAt,comments"]))
        except Exception as e:
            print(f"[railway_watch] view #{n} failed: {e}", file=sys.stderr)
            if str(n) in state:
                fresh[str(n)] = state[str(n)]      # keep prior state; try next tick
            continue
        updated = data.get("updatedAt", "")
        comments = data.get("comments") or []
        latest_author = (comments[-1].get("author") or {}).get("login") if comments else None
        prev = state.get(str(n))
        fire = prev is None or (updated != prev.get("updatedAt") and latest_author != my_login)
        fresh[str(n)] = {"updatedAt": updated}
        if fire:
            title = data.get("title", "")
            _inject(
                f"[bloopy-network railway] Issue #{n} \"{title}\" has new activity that may need a "
                f"Railway/infra action. Read it with `gh issue view {n} --repo {REPO} --comments`, "
                f"perform the action via the railway CLI / gh, comment the result back on the issue, "
                f"then remove the `railway` label (or close it) once done. If nothing is actually "
                f"actionable, post a short note and clear the label. Ignore issue #9. "
                f"Message Yishai only if you took an action or need a decision.")
            print(f"[railway_watch] injected for #{n}", file=sys.stderr)
    return fresh


def _log(msg: str) -> None:
    """Runs under pythonw, so stderr goes nowhere — persist to a file instead,
    otherwise this sidecar can fail silently for weeks."""
    try:
        line = f"{time.strftime('%Y-%m-%d %H:%M:%S')}  {msg}\n"
        p = STATE_DIR / "railway_watch.log"
        if p.exists() and p.stat().st_size > 1_000_000:
            p.replace(p.with_suffix(".log.old"))
        with p.open("a", encoding="utf-8") as fh:
            fh.write(line)
    except OSError:
        pass


def main():
    once = "--once" in sys.argv
    my_login = _my_login()
    _log(f"started (poll={POLL_SECONDS}s, repo={REPO}, login={my_login})")
    fails = 0
    while True:
        try:
            state = check_once(my_login, _load_state())
            _save_state(state)
            fails = 0
        except Exception as e:
            fails += 1
            _log(f"tick error #{fails}: {type(e).__name__}: {e}")
            print(f"[railway_watch] tick error: {e}", file=sys.stderr)
            if fails >= 20:
                _log("20 consecutive failures — backing off 10 min")
                time.sleep(600)
                fails = 0
        if once:
            return
        time.sleep(POLL_SECONDS)


if __name__ == "__main__":
    main()
