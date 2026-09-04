#!/bin/bash
# Reconcile the widget's launchd background agents to the CURRENT install.
# Idempotent, and called from BOTH install.sh and update.sh - the point is that
# updates used to pull only code, so background jobs never got fixed (or removed)
# on machines that update via git pull. This converges the machine to the intended
# state every time.
#
# There is exactly ONE agent: the 30-minute cookie refresher. The weekly API-rate
# check is driven from the plugin now (self-healing when stale), so there is no
# separate pricing agent to drift or fail to install.
#
# Usage: agents.sh [install-dir]   (defaults to this script's own repo root)

set -u
DIR="${1:-$(cd "$(dirname "${BASH_SOURCE[0]:-$0}")" && pwd)}"
LA="$HOME/Library/LaunchAgents"
UID_="$(id -u)"
mkdir -p "$LA"

boot_out() {  # unload a label if loaded (modern then legacy), then remove its plist
  local label="$1"
  launchctl bootout "gui/$UID_/$label" 2>/dev/null \
    || launchctl unload "$LA/$label.plist" 2>/dev/null || true
  rm -f "$LA/$label.plist" 2>/dev/null
}

# Retire superseded / duplicate agents:
#  - com.darren.claude-usage-refresh: the original label, pointed at a dev copy,
#    and ran a SECOND refresher every 30 min alongside the current one.
#  - com.claudometer.pricing: the weekly pricing job, now plugin-driven.
boot_out "com.darren.claude-usage-refresh"
boot_out "com.claudometer.pricing"

# The one agent we keep: the cookie refresher. A short keychain timeout so a
# background run never hangs on a permission prompt (interactive setup keeps the
# 90s default via the env var being unset there).
# XML-escape any value embedded in the plist. A path containing & < > would
# otherwise produce an invalid plist that silently fails to load, leaving zero
# refreshers while we print "active".
xml_escape() { printf '%s' "$1" | sed -e 's/&/\&amp;/g' -e 's/</\&lt;/g' -e 's/>/\&gt;/g'; }

REFRESH="com.claudometer.refresh"
PLIST="$LA/$REFRESH.plist"
PY_X=$(xml_escape "$DIR/.venv/bin/python")
RUN_X=$(xml_escape "$DIR/refresh_cookie.py")
HOME_X=$(xml_escape "$HOME")
cat > "$PLIST" <<PL
<?xml version="1.0" encoding="UTF-8"?>
<!DOCTYPE plist PUBLIC "-//Apple//DTD PLIST 1.0//EN" "http://www.apple.com/DTDs/PropertyList-1.0.dtd">
<plist version="1.0"><dict>
  <key>Label</key><string>$REFRESH</string>
  <key>ProgramArguments</key><array>
    <string>$PY_X</string>
    <string>$RUN_X</string>
  </array>
  <key>StartInterval</key><integer>1800</integer>
  <key>RunAtLoad</key><true/>
  <key>StandardErrorPath</key><string>/tmp/claude-usage-refresh.err</string>
  <key>EnvironmentVariables</key><dict>
    <key>HOME</key><string>$HOME_X</string>
    <key>CLAUDE_KEYCHAIN_TIMEOUT_SECONDS</key><string>8</string>
  </dict>
</dict></plist>
PL
# Reload so a changed path or interval actually takes effect.
launchctl bootout "gui/$UID_/$REFRESH" 2>/dev/null || launchctl unload "$PLIST" 2>/dev/null || true
launchctl bootstrap "gui/$UID_" "$PLIST" 2>/dev/null || launchctl load "$PLIST" 2>/dev/null || true

# Verify it actually loaded - never claim success blindly.
if launchctl print "gui/$UID_/$REFRESH" >/dev/null 2>&1 || launchctl list 2>/dev/null | grep -q "$REFRESH"; then
  echo "launchd: $REFRESH active; retired legacy refresher and any pricing agent."
else
  echo "WARNING: could not load $REFRESH - the widget still works, cookies just refresh when you open the menu." >&2
  exit 1
fi
