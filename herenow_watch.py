#!/usr/bin/env python3
"""Watch the owner's here.now account and announce changes on the ops bot.

One call to GET /api/v1/publishes per tick, diffed against the last snapshot.
A site is "changed" when its currentVersionId moves — updatedAt alone also ticks
for metadata edits, and announcing those would make the channel noisy enough to
be ignored, which is the only real failure mode for a notifier.

First run seeds the snapshot silently: 21 sites already existed when this was
written, and a backlog dump on install teaches the user to mute the bot.
"""

import json
import os
import subprocess
import sys
import urllib.error
import urllib.request
from pathlib import Path

ALFRED = Path("/home/ubuntu/alfred")
STATE = ALFRED / "state" / "herenow-sites.json"
NOTIFY = ALFRED / "opsnotify.sh"
API = "https://here.now/api/v1/publishes?limit=200"


def notify(msg: str) -> None:
    try:
        subprocess.run([str(NOTIFY), msg], check=True, capture_output=True, timeout=30)
    except Exception as e:  # noqa: BLE001 - a broken notifier must not kill the watcher
        print(f"herenow-watch: notify failed: {e}", file=sys.stderr)


def api_key() -> str:
    """Resolve the key through Secretbox rather than reading a file on disk.

    systemd runs this unit with no HERENOW_API_KEY in the environment, so the
    normal path is the resolve() below. The env branch exists so the whole
    script can also be run under `secretbox.py --run HERENOW_API_KEY -- ...`
    without resolving twice.
    """
    env = os.environ.get("HERENOW_API_KEY")
    if env:
        return env.strip()
    sys.path.insert(0, str(ALFRED))
    import secretbox  # noqa: PLC0415 - only needed on the non-env path
    val, _src, _cands = secretbox.resolve("here-now/HERENOW_API_KEY")
    if not val:
        raise RuntimeError("here-now/HERENOW_API_KEY is not in Secretbox")
    return val.strip()


def fetch() -> dict:
    key = api_key()
    req = urllib.request.Request(API, headers={
        "Authorization": f"Bearer {key}",
        "User-Agent": "alfred-herenow-watch/1.0",
    })
    with urllib.request.urlopen(req, timeout=45) as r:
        data = json.loads(r.read().decode("utf-8"))
    out = {}
    for s in data.get("publishes", []):
        slug = s.get("slug")
        if slug:
            out[slug] = {
                "url": s.get("siteUrl") or f"https://{slug}.here.now/",
                "version": s.get("currentVersionId"),
                "status": s.get("status"),
                "updated": s.get("updatedAt"),
                "expires": s.get("expiresAt"),
            }
    return out


def add_names(sites: dict, known: dict) -> None:
    """Fill in each site's display name, fetching detail only for new slugs.

    The list endpoint doesn't carry displayName, and re-fetching detail for all
    21 sites every five minutes to learn names that never change would be 21
    calls a tick for nothing. Names are looked up once, then carried forward.
    """
    key = api_key()
    for slug, rec in sites.items():
        prior = (known or {}).get(slug, {}).get("name")
        if prior:
            rec["name"] = prior
            continue
        req = urllib.request.Request(
            f"https://here.now/api/v1/publish/{slug}",
            headers={"Authorization": f"Bearer {key}",
                     "User-Agent": "alfred-herenow-watch/1.0"})
        try:
            with urllib.request.urlopen(req, timeout=30) as r:
                rec["name"] = json.loads(r.read().decode("utf-8")).get("displayName") or slug
        except Exception:
            rec["name"] = slug


def load_state() -> dict:
    try:
        return json.loads(STATE.read_text(encoding="utf-8"))
    except Exception:
        return {}


def save_state(sites: dict, failing: bool) -> None:
    STATE.parent.mkdir(parents=True, exist_ok=True)
    tmp = STATE.with_suffix(".tmp")
    tmp.write_text(json.dumps({"sites": sites, "failing": failing}, indent=1),
                   encoding="utf-8")
    tmp.replace(STATE)


def main() -> int:
    state = load_state()
    known = state.get("sites")
    was_failing = bool(state.get("failing"))

    try:
        current = fetch()
    except Exception as e:  # noqa: BLE001
        # Announce only the transition into failure — a dead API would otherwise
        # send one message every five minutes forever.
        if not was_failing:
            notify(f"⚠️ here.now watcher cannot read the account: {e}")
        save_state(known or {}, failing=True)
        return 1

    if was_failing:
        notify("✅ here.now watcher is reading the account again.")

    add_names(current, known)

    if known is None:
        save_state(current, failing=False)
        print(f"herenow-watch: seeded {len(current)} sites, no notification sent")
        return 0

    new = [s for s in current if s not in known]
    gone = [s for s in known if s not in current]
    changed = [s for s in current
               if s in known and current[s]["version"] != known[s].get("version")]

    # Record when the CONTENT last changed, which is not what updatedAt means:
    # setting a password bumps updatedAt without touching a byte of the page, so
    # a freshness indicator built on updatedAt reports a week-old report as new.
    # Only a change of currentVersionId is a real republish.
    for slug in current:
        prior = (known or {}).get(slug, {})
        current[slug]["content_at"] = (
            current[slug]["updated"] if slug in changed or slug in new
            else prior.get("content_at"))

    lines = []
    for slug in sorted(new):
        exp = current[slug].get("expires")
        tail = "  ⏳ פג תוקף בעוד 24 שעות (אתר אנונימי)" if exp else ""
        lines.append(f"🆕 אתר חדש: {slug}\n{current[slug]['url']}{tail}")
    for slug in sorted(changed):
        lines.append(f"♻️ עודכן: {slug}\n{current[slug]['url']}")
    for slug in sorted(gone):
        lines.append(f"🗑️ נמחק: {slug}")

    if lines:
        notify("here.now — עדכון בחשבון שלך\n\n" + "\n\n".join(lines))
        print(f"herenow-watch: notified {len(lines)} change(s)")
    else:
        print("herenow-watch: no change")

    save_state(current, failing=False)
    return 0


if __name__ == "__main__":
    sys.exit(main())
