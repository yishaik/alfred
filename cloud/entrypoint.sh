#!/usr/bin/env bash
set -Eeuo pipefail

DATA_DIR="${ALFRED_DATA_DIR:-/data}"
APP_DIR="/app"
STATE_DIR="${DATA_DIR}/state"
WORKSPACES_DIR="${DATA_DIR}/workspaces"
HOME_DIR="${DATA_DIR}/home"

mkdir -p "$STATE_DIR" "$WORKSPACES_DIR" "$HOME_DIR/.claude" "$HOME_DIR/.codex" "$HOME_DIR/.gemini" "$HOME_DIR/.config"

# Migrate image-bundled state only once, then keep all runtime state on the volume.
if [[ -d "$APP_DIR/state" && ! -L "$APP_DIR/state" ]]; then
  if [[ -z "$(find "$STATE_DIR" -mindepth 1 -maxdepth 1 -print -quit 2>/dev/null)" ]]; then
    cp -a "$APP_DIR/state/." "$STATE_DIR/" 2>/dev/null || true
  fi
  rm -rf "$APP_DIR/state"
fi
ln -sfn "$STATE_DIR" "$APP_DIR/state"

# Global instructions make deployment tools discoverable to the existing Claude SDK session.
install -m 0600 "$APP_DIR/cloud/prompts/CLAUDE.md" "$HOME_DIR/.claude/CLAUDE.md"

chown -R alfred:alfred "$DATA_DIR"
export HOME="$HOME_DIR"
export BRIDGE_WORKDIR="${BRIDGE_WORKDIR:-$WORKSPACES_DIR}"
export PYTHONPATH="$APP_DIR${PYTHONPATH:+:$PYTHONPATH}"

# Bootstrap current provider aliases without overwriting an operator-edited router config.
gosu alfred python -m cloud.bootstrap_router

exec gosu alfred "$@"
