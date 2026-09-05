#!/bin/bash
# <bitbar.title>Claude Usage</bitbar.title>
# <bitbar.version>v3.0</bitbar.version>
# <bitbar.author>Darren</bitbar.author>
# <bitbar.desc>Claude.ai + Claude Code + Codex usage in the menu bar.</bitbar.desc>
# <bitbar.dependencies>bash,python3,curl_cffi</bitbar.dependencies>
#
# Thin shim: resolve paths, rotate logs, fetch the usage payload (with cookie
# auto-recovery and offline handling), then hand the ENTIRE render to
# render_menu.py. Everything that used to be ~940 lines of bash - parsing,
# formatting, image rendering, prediction, status, alerts, the footer - now
# lives in render_menu.py, which is testable from a fixture. This file only does
# the parts that are genuinely shell-shaped: process spawning and the pre-render
# failure tiles (nothing to render yet when the fetch itself fails).

set -o pipefail

WIDGET_DIR="${CLAUDE_WIDGET_DIR:-$(cd "$(dirname "${BASH_SOURCE[0]:-$0}")/.." 2>/dev/null && pwd)}"
PYTHON="$WIDGET_DIR/.venv/bin/python"
FETCHER="$WIDGET_DIR/fetch_usage.py"
REFRESHER="$WIDGET_DIR/refresh_cookie.py"
RENDERER="$WIDGET_DIR/render_menu.py"
CONFIG="$HOME/.claude-usage-widget.conf"
RAW="/tmp/claude-usage-raw.json"
ERR_LOG="/tmp/claude-usage-err.log"
REFRESH_LOG="/tmp/claude-usage-refresh.log"
REFRESH_ERR="/tmp/claude-usage-refresh.err"
LOG_MAX_BYTES=1048576

# Rotate any oversized log to .1 and truncate. Cheap; runs every tick.
for f in "$ERR_LOG" "$REFRESH_LOG" "$REFRESH_ERR"; do
  if [ -f "$f" ]; then
    size=$(stat -f%z "$f" 2>/dev/null || echo 0)
    [ "$size" -gt "$LOG_MAX_BYTES" ] && { mv -f "$f" "$f.1"; : > "$f"; }
  fi
done

# System appearance: LBL for the failure tiles below, and exported so the
# renderer paints its labels and picks its track colours to match.
if defaults read -g AppleInterfaceStyle 2>/dev/null | grep -qi dark; then
  LBL="#f2f2f7"; export CLAUDE_APPEARANCE=dark
else
  LBL="#1c1c1e"; export CLAUDE_APPEARANCE=light
fi

# --- Guards ------------------------------------------------------------------
if [ ! -x "$PYTHON" ] || [ ! -f "$FETCHER" ] || [ ! -f "$RENDERER" ]; then
  echo "⚠ Setup | sfimage=exclamationmark.triangle color=#FF9500"
  echo "---"
  echo "Python venv or plugin files missing"
  echo "Expected: $PYTHON"
  echo "Expected: $RENDERER"
  exit 0
fi
if [ ! -f "$CONFIG" ]; then
  echo "⚠ Config | sfimage=exclamationmark.triangle color=#FF9500"
  echo "---"
  echo "Create config | bash='/usr/bin/open' param1='-t' param2='$CONFIG' terminal=false refresh=true"
  exit 0
fi

# --- Fetch (with auto-recovery) ----------------------------------------------
# fetch_usage.py writes empty stdout on any failure, the reason to stderr.
RESP=$("$PYTHON" "$FETCHER" 2>"$ERR_LOG")
FETCH_EXIT=$?
REFRESH_RC=0
if [ $FETCH_EXIT -ne 0 ] || [ -z "$RESP" ]; then
  "$PYTHON" "$REFRESHER" 2>>"$ERR_LOG"
  REFRESH_RC=$?
  if [ $REFRESH_RC -eq 0 ]; then
    RESP=$("$PYTHON" "$FETCHER" 2>>"$ERR_LOG")
    FETCH_EXIT=$?
  fi
fi

# Still failing: react to WHY, using refresh_cookie's exit codes (2 session
# invalid, 3 network/offline, 4 keychain) so offline keeps the last numbers.
OFFLINE=0
if [ $FETCH_EXIT -ne 0 ] || [ -z "$RESP" ]; then
  ERR=$(tail -c 240 "$ERR_LOG" 2>/dev/null | tr '\n' ' ')
  SESSION_DEAD=0
  { [ "$REFRESH_RC" -eq 2 ] || echo "$ERR" | grep -q "account_session_invalid\|No valid Claude session"; } && SESSION_DEAD=1

  if [ "$SESSION_DEAD" -eq 1 ]; then
    echo "⚠ Re-auth | sfimage=person.badge.key color=#FF9500"
    echo "---"
    echo "Session expired. Sign in to the Claude desktop app, or claude.ai in | size=12 color=$LBL"
    echo "Chrome / Arc / Brave. The widget recovers on its own. | size=12 color=$LBL"
    echo "---"
    echo "Open Claude | href=https://claude.ai/login sfimage=safari"
    echo "Force cookie refresh | bash='$PYTHON' param1='$REFRESHER' terminal=false refresh=true sfimage=key.fill"
    echo "View error log | bash='/usr/bin/open' param1='-t' param2='$ERR_LOG' terminal=false sfimage=exclamationmark.bubble"
    exit 0
  elif [ "$REFRESH_RC" -eq 4 ]; then
    echo "⚠ Keychain | sfimage=key color=#FF9500"
    echo "---"
    echo "Approve the macOS Keychain prompt (click Always Allow) so the widget | size=12 color=$LBL"
    echo "can read your Claude session. | size=12 color=$LBL"
    echo "---"
    echo "Force cookie refresh | bash='$PYTHON' param1='$REFRESHER' terminal=false refresh=true sfimage=key.fill"
    echo "View error log | bash='/usr/bin/open' param1='-t' param2='$ERR_LOG' terminal=false sfimage=exclamationmark.bubble"
    exit 0
  elif [ -s "$RAW" ]; then
    RESP=$(cat "$RAW"); OFFLINE=1
  else
    echo "⏸ Offline | sfimage=wifi.slash color=#8E8E93"
    echo "---"
    echo "Can't reach claude.ai and there is no cached data yet. | size=12 color=$LBL"
    echo "It recovers on its own once you are back online. | size=12 color=$LBL"
    echo "---"
    echo "View error log | bash='/usr/bin/open' param1='-t' param2='$ERR_LOG' terminal=false sfimage=exclamationmark.bubble"
    exit 0
  fi
fi

# --- Render ------------------------------------------------------------------
# Hand the payload to the renderer. It parses, formats, draws the graphics in
# process, fires alerts, appends history, and prints every menu line. Capture its
# output so that if the renderer dies before printing anything (e.g. an import
# error, so its own fallback never runs), the menu bar still shows something
# clickable instead of going blank.
if [ "$OFFLINE" -eq 1 ]; then
  MENU=$(printf '%s' "$RESP" | "$PYTHON" "$RENDERER" --offline 2>>"$ERR_LOG")
else
  MENU=$(printf '%s' "$RESP" | "$PYTHON" "$RENDERER" 2>>"$ERR_LOG")
fi
if [ -n "$MENU" ]; then
  printf '%s\n' "$MENU"
else
  echo "⚠ Claude | sfimage=exclamationmark.circle color=#FF9500 size=12"
  echo "---"
  echo "The renderer produced no output. It usually clears next tick. | size=12 color=$LBL"
  echo "View error log | bash='/usr/bin/open' param1='-t' param2='$ERR_LOG' terminal=false"
  echo "↻ Refresh now | refresh=true sfimage=arrow.clockwise"
fi
