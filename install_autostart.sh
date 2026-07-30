#!/usr/bin/env bash
# macOS autostart installer — the counterpart to install_autostart.bat.
#
# Registers a launchd LaunchAgent that runs supervisor.py (which owns crash-loop
# backoff and bridge.log rotation) at login and keeps it alive.
#
#   ./install_autostart.sh            install + start
#   ./install_autostart.sh --uninstall  stop + remove
#
# Why an Agent and not a Daemon: Claude Code auth lives in the user profile
# (~/.claude/.credentials.json), so the bridge must run as you, not root. This
# is the same reason install_autostart.bat uses a per-user scheduled task.
set -euo pipefail

LABEL="com.yishaik.alfred"
ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
PLIST="$HOME/Library/LaunchAgents/$LABEL.plist"
DOMAIN="gui/$(id -u)"

if [[ "$(uname -s)" != "Darwin" ]]; then
  echo "This installer is macOS-only. On Windows use install_autostart.bat." >&2
  exit 1
fi

if [[ "${1:-}" == "--uninstall" ]]; then
  launchctl bootout "$DOMAIN/$LABEL" 2>/dev/null || true
  rm -f "$PLIST"
  echo "Removed $LABEL."
  exit 0
fi

PYTHON="$ROOT/.venv/bin/python"
if [[ ! -x "$PYTHON" ]]; then
  echo "No venv at $PYTHON. Create it first:" >&2
  echo "  uv venv --python 3.13 --python-preference only-managed .venv" >&2
  echo "  uv pip install --python .venv/bin/python -r requirements.txt" >&2
  exit 1
fi

if [[ ! -f "$ROOT/.env" ]]; then
  echo "No .env next to bridge.py — the bridge needs BRIDGE_BOT_TOKEN and BRIDGE_CHAT_ID." >&2
  exit 1
fi

# launchd hands the job a bare PATH and never reads .zshrc, so every directory
# holding something the bridge shells out to (claude, node/npm for napkin, git)
# has to be named here. Derive from the *current* shell so version managers
# (asdf/nvm/homebrew) keep working instead of being hardcoded.
detect_dir() { local p; p="$(command -v "$1" 2>/dev/null)" && dirname "$p" || true; }
EXTRA_PATH=""
for tool in claude node npm git; do
  d="$(detect_dir "$tool")"
  [[ -n "$d" && ":$EXTRA_PATH:" != *":$d:"* ]] && EXTRA_PATH="${EXTRA_PATH:+$EXTRA_PATH:}$d"
done
# asdf shims re-exec through the asdf binary, so its bin dir must come along.
[[ -d "$HOME/.asdf/bin" ]] && EXTRA_PATH="$EXTRA_PATH:$HOME/.asdf/bin"
FULL_PATH="${EXTRA_PATH:+$EXTRA_PATH:}/opt/homebrew/bin:/opt/homebrew/sbin:/usr/local/bin:/usr/bin:/bin:/usr/sbin:/sbin"

mkdir -p "$HOME/Library/LaunchAgents" "$ROOT/state"

cat > "$PLIST" <<PLIST_EOF
<?xml version="1.0" encoding="UTF-8"?>
<!DOCTYPE plist PUBLIC "-//Apple//DTD PLIST 1.0//EN" "http://www.apple.com/DTDs/PropertyList-1.0.dtd">
<plist version="1.0">
<dict>
  <key>Label</key><string>$LABEL</string>
  <key>ProgramArguments</key>
  <array>
    <string>$PYTHON</string>
    <string>-u</string>
    <string>$ROOT/supervisor.py</string>
  </array>
  <key>WorkingDirectory</key><string>$ROOT</string>
  <key>RunAtLoad</key><true/>
  <!-- supervisor.py traps SIGTERM and exits 0, so a deliberate \`launchctl
       bootout\` must NOT respawn it. Only a non-zero (crash) exit restarts. -->
  <key>KeepAlive</key>
  <dict><key>SuccessfulExit</key><false/></dict>
  <key>ThrottleInterval</key><integer>30</integer>
  <key>ProcessType</key><string>Standard</string>
  <key>EnvironmentVariables</key>
  <dict>
    <key>PATH</key><string>$FULL_PATH</string>
    <key>HOME</key><string>$HOME</string>
    <key>PYTHONUTF8</key><string>1</string>
    <key>LANG</key><string>en_US.UTF-8</string>
    <key>LC_ALL</key><string>en_US.UTF-8</string>
  </dict>
  <!-- Only catches pre-loop failures; normal output goes to bridge.log, which
       supervisor.py rotates. launchd does not rotate these. -->
  <key>StandardOutPath</key><string>$ROOT/state/launchd.out.log</string>
  <key>StandardErrorPath</key><string>$ROOT/state/launchd.err.log</string>
</dict>
</plist>
PLIST_EOF

plutil -lint "$PLIST" >/dev/null

launchctl bootout "$DOMAIN/$LABEL" 2>/dev/null || true   # replace any old copy
launchctl bootstrap "$DOMAIN" "$PLIST"

echo "Installed $LABEL"
echo "  status : launchctl print $DOMAIN/$LABEL"
echo "  logs   : tail -f $ROOT/bridge.log"
echo "  stop   : launchctl bootout $DOMAIN/$LABEL"
echo
echo "Note: a LaunchAgent starts at LOGIN, not at boot. For unattended restarts"
echo "enable automatic login (System Settings > Users & Groups)."
