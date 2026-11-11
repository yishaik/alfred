#!/usr/bin/env bash
set -Eeuo pipefail

cat <<'MSG'
Run these commands inside `fly ssh console` after the /data volume is mounted:

  claude             # choose Claude App / Pro or Max login
  codex login        # choose ChatGPT account login
  gemini              # choose Google account login

Complete each browser/device authorization prompt. Credentials persist under /data/home.
Then run:

  claude doctor
  codex --version
  gemini --version
  python -m cloud.platformctl doctor
MSG
