#!/usr/bin/env python3
"""peercheck — prove the peer bus works, and say so only when it does not.

Reachability is not health. A listener can accept TCP while rejecting every
token, and a token can authenticate while the peer never processes anything.
This checks the layer that actually matters: does each peer ACCEPT the token we
would really send it, and does it REJECT one it should not.

    python3 peercheck.py            # print the matrix
    python3 peercheck.py --notify   # stay silent unless something regressed

Run from cron or a timer. Silence means working — an alert that arrives daily
regardless is one nobody reads.

Probes use hop=9, above MAX_HOPS, so an accepting peer authenticates the
message and then drops it before it reaches an agent. Checking the bus must not
cost anyone a turn.
"""
import json
import os
import subprocess
import sys
import urllib.error
import urllib.request
from pathlib import Path

ALFRED = Path(__file__).resolve().parent
STATE = ALFRED / "state" / "peercheck.json"


def env(path=ALFRED / ".env") -> dict:
    out = {}
    for line in path.read_text(encoding="utf-8").splitlines():
        if "=" in line and not line.lstrip().startswith("#"):
            k, v = line.split("=", 1)
            out[k.strip()] = v.strip()
    return out


def post(url: str, token: str, timeout: float = 8.0) -> int:
    body = json.dumps({"token": token, "from": "peercheck/probe", "agent": "",
                       "text": "[peercheck]", "hop": 9}).encode()
    req = urllib.request.Request(url.rstrip("/") + "/msg", data=body,
                                 headers={"Content-Type": "application/json"})
    try:
        with urllib.request.urlopen(req, timeout=timeout) as r:
            return r.status
    except urllib.error.HTTPError as e:
        return e.code
    except Exception:
        return 0


def main(argv):
    e = env()
    peers = {}
    for part in e.get("BRIDGE_PEERS", "").split(";"):
        if "=" in part:
            n, _, u = part.partition("=")
            peers[n.strip()] = u.strip()
    tokens = {}
    for part in e.get("BRIDGE_PEER_TOKENS", "").split(";"):
        if "=" in part:
            n, _, t = part.partition("=")
            tokens[n.strip()] = t.strip()
    legacy = {p.strip() for p in e.get("BRIDGE_PEER_LEGACY", "").replace(";", ",").split(",") if p.strip()}
    me = e.get("BRIDGE_PEER_NAME", "")
    self_token = tokens.get(me, "")

    rows, bad = [], []
    for name, url in peers.items():
        # Exactly what send() would present to this peer.
        tok = (tokens.get(name) if name in legacy else None) or self_token
        good = post(url, tok)
        # A token this peer must not accept — proves it is checking at all
        # rather than waving everything through.
        wrong = post(url, "peercheck-should-be-rejected-000000000000")
        ok = good == 200 and wrong == 403
        rows.append((name, url, good, wrong, ok))
        if not ok:
            why = ("unreachable" if good == 0 else
                   f"rejects our token ({good})" if good != 200 else
                   f"accepts a bad token ({wrong}) — not authenticating")
            bad.append(f"{name}: {why}")

    if "--notify" not in argv:
        print(f"peer bus — this bridge is {me!r}\n")
        for name, url, good, wrong, ok in rows:
            print(f"  {'🟢' if ok else '🔴'} {name:<14} ours={good} bogus={wrong}  {url}")
        q = ALFRED / "state" / "peer-queue.json"
        try:
            pending = len(json.loads(q.read_text(encoding="utf-8")))
        except Exception:
            pending = 0
        print(f"\n  undelivered queued: {pending}")
        return 1 if bad else 0

    # Only speak when the state CHANGED. A peer that has been down for a week
    # is not news every hour; a peer that just went down is.
    try:
        prev = json.loads(STATE.read_text(encoding="utf-8"))
    except Exception:
        prev = {}
    now = {n: ok for n, _, _, _, ok in rows}
    try:
        STATE.parent.mkdir(parents=True, exist_ok=True)
        STATE.write_text(json.dumps(now), encoding="utf-8")
    except Exception:
        pass
    changed = [n for n, ok in now.items() if prev.get(n) != ok]
    if changed and bad:
        subprocess.run([str(ALFRED / "opsnotify.sh"),
                        "🔌 peer bus changed:\n" + "\n".join(f"· {b}" for b in bad)],
                       capture_output=True, timeout=25)
    elif changed and not bad:
        subprocess.run([str(ALFRED / "opsnotify.sh"),
                        "🔌 peer bus: all peers healthy again"],
                       capture_output=True, timeout=25)
    return 1 if bad else 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))
