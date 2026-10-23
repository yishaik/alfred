#!/usr/bin/env bash
# push a heartbeat timestamp to the public gist for this box
GID=$(cat /home/ubuntu/alfred/state/oci-watchdog-gist.txt)
BOX="${1:-$(hostname)}"
TS=$(date -u +%Y-%m-%dT%H:%M:%SZ)
gh api -X PATCH "gists/$GID" -f "files[hb-${BOX}.txt][content]=${TS}" >/dev/null 2>&1
