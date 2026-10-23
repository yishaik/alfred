#!/usr/bin/env bash
# Watch hermes' services from alfred and alert the ops bot ONLY on state change.
# Runs from a systemd timer every 5 minutes. Checks, over SSH:
#   - box reachability itself (an unreachable box is the loudest state of all)
#   - openclaw-gateway + hermes-gateway (user units) and cliproxy (system unit)
#   - the OpenClaw gateway HTTP endpoint on 127.0.0.1:18789
# State lives in state/hermes-watch.json; no secrets leave this box.
set -uo pipefail
cd /home/ubuntu/alfred

STATE=state/hermes-watch.json
SSH="ssh -i /home/ubuntu/.ssh/hermes_oracle -o BatchMode=yes -o ConnectTimeout=10 -o StrictHostKeyChecking=accept-new ubuntu@100.103.98.82"

REMOTE='export XDG_RUNTIME_DIR=/run/user/1001
oc=$(systemctl --user is-active openclaw-gateway 2>/dev/null)
hg=$(systemctl --user is-active hermes-gateway 2>/dev/null)
om=$(systemctl --user is-active omniroute 2>/dev/null)
cp=$(systemctl is-active cliproxy 2>/dev/null)
gw=$(curl -s -o /dev/null -w "%{http_code}" --max-time 5 http://127.0.0.1:18789/ 2>/dev/null)
og=$(curl -s -o /dev/null -w "%{http_code}" --max-time 5 http://127.0.0.1:20128/v1/models 2>/dev/null)
printf "{\"box\":\"up\",\"openclaw\":\"%s\",\"hermes\":\"%s\",\"omniroute\":\"%s\",\"cliproxy\":\"%s\",\"gateway_http\":\"%s\",\"omniroute_http\":\"%s\"}" "$oc" "$hg" "$om" "$cp" "$gw" "$og"'

now=$($SSH "$REMOTE" 2>/dev/null)
[ -z "$now" ] && now='{"box":"down","openclaw":"?","hermes":"?","cliproxy":"?","gateway_http":"?"}'

prev=$(cat "$STATE" 2>/dev/null || echo '{}')
if [ "$now" == "$prev" ]; then exit 0; fi   # no change — stay quiet

echo "$now" > "$STATE"

msg=$(python3 - "$prev" "$now" <<'PY'
import json, sys
try:
    prev = json.loads(sys.argv[1])
except Exception:
    prev = {}
now = json.loads(sys.argv[2])

if not prev:          # first run — record baseline silently
    sys.exit(0)

def ok(key, v):
    if key == "box": return v == "up"
    if key.endswith("_http"): return v == "200"
    return v == "active"

names = {"box": "hermes (המכונה)", "openclaw": "OpenClaw", "hermes": "Hermes agent",
         "omniroute": "OmniRoute", "cliproxy": "CLIProxyAPI",
         "gateway_http": "OpenClaw HTTP", "omniroute_http": "OmniRoute HTTP"}
lines = []
for k, label in names.items():
    was, cur = prev.get(k), now.get(k)
    if was == cur: continue
    lines.append((f"✅ {label} חזר" if ok(k, cur) else f"🔴 {label} נפל") + f" ({cur})")
if lines:
    print("🛰 hermes watch:\n" + "\n".join(lines))
PY
)
[ -n "$msg" ] && ./opsnotify.sh "$msg"
exit 0
