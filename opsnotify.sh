#!/usr/bin/env bash
# Canonical outbound notification for anything on this box that is NOT the
# bridge itself: cron jobs, systemd units, the understudy queue, backups.
#
# Sends via @AlfredOps2bot (the managed ops bot) so machine chatter stays out of
# the conversation with Alfred. Falls back to Alfred's own bot if OPS_BOT_TOKEN
# is missing, so a half-configured box still gets its alerts.
#
# Usage:  opsnotify.sh "message"
#         some-command 2>&1 | opsnotify.sh
set -euo pipefail

ENV_FILE="${ALFRED_ENV:-/home/ubuntu/alfred/.env}"

msg="${1:-}"
[ -z "$msg" ] && msg="$(cat)"
[ -z "$msg" ] && { echo "opsnotify: empty message" >&2; exit 1; }

[ -r "$ENV_FILE" ] || { echo "opsnotify: cannot read $ENV_FILE" >&2; exit 1; }
val() { grep -m1 "^$1=" "$ENV_FILE" | cut -d= -f2- | tr -d '"'"'"' \r'; }

TOKEN="$(val OPS_BOT_TOKEN)"; [ -n "$TOKEN" ] || TOKEN="$(val BRIDGE_BOT_TOKEN)"
CHAT="$(val BRIDGE_CHAT_ID)"
[ -n "$TOKEN" ] && [ -n "$CHAT" ] || { echo "opsnotify: token or chat id missing" >&2; exit 1; }

code=$(curl -sS -o /tmp/opsnotify-resp.json -w '%{http_code}' \
  "https://api.telegram.org/bot${TOKEN}/sendMessage" \
  --data-urlencode "chat_id=${CHAT}" \
  --data-urlencode "text=${msg}" \
  -d "disable_web_page_preview=true")

[ "$code" = "200" ] || { echo "opsnotify: HTTP $code — $(head -c 200 /tmp/opsnotify-resp.json)" >&2; exit 1; }
echo "opsnotify: sent"
