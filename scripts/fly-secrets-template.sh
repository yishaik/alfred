#!/usr/bin/env bash
set -Eeuo pipefail

APP="${1:?usage: $0 <fly-app-name>}"

cat <<EOF2
Set values in your shell or password manager, then run a command shaped like this:

fly secrets set --app "$APP" \\
  BRIDGE_BOT_TOKEN='***' \\
  BRIDGE_CHAT_ID='***' \\
  GH_TOKEN='***' \\
  ALFRED_ALLOWED_REPOS='yishaik/alfred' \\
  XAI_API_KEY='***' \\
  TAVILY_API_KEY='***'

Add VERCEL_TOKEN, NETLIFY_AUTH_TOKEN, CLOUDFLARE_API_TOKEN,
SUPABASE_ACCESS_TOKEN, HF_TOKEN and OPENAI_API_KEY only when needed.
EOF2
