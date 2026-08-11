#!/usr/bin/env bash
# Ship persona-lab's Public Mind site: rsync the built dist from hermes and
# deploy with alfred's authenticated Vercel CLI when the content changes.
set -euo pipefail
DIST=/home/ubuntu/alfred/state/persona-site-dist
STATE=/home/ubuntu/alfred/state/.persona-site-hash
mkdir -p "$DIST"
rsync -az --delete -e "ssh -i /home/ubuntu/.ssh/hermes_oracle -o BatchMode=yes" \
  ubuntu@10.0.0.177:/opt/persona-lab/data/site_dist/ "$DIST/" 2>/dev/null || exit 0
[ -f "$DIST/index.html" ] || exit 0
HASH=$(find "$DIST" -type f -print0 | sort -z | xargs -0 sha256sum | sha256sum | cut -d" " -f1)
[ -f "$STATE" ] && [ "$(cat "$STATE")" = "$HASH" ] && exit 0
cd "$DIST"
vercel deploy --prod --yes --name synapse-soma 2>&1 | tail -1
echo "$HASH" > "$STATE"
