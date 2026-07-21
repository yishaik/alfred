#!/usr/bin/env bash
set -Eeuo pipefail
APP="${1:?usage: $0 <fly-app-name>}"

fly status --app "$APP"
fly checks list --app "$APP"
fly ssh console --app "$APP" --command 'python -m cloud.platformctl doctor'
