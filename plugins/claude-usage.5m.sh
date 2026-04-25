#!/bin/bash
# <bitbar.title>Claude Usage</bitbar.title>
# <bitbar.version>v2.0</bitbar.version>
# <bitbar.author>Darren</bitbar.author>
# <bitbar.desc>Claude.ai usage dashboard in menu bar with visual bars, color alerts, auto cookie recovery.</bitbar.desc>
# <bitbar.dependencies>bash,jq,python3,curl_cffi</bitbar.dependencies>
#
# SwiftBar: filename suffix = refresh interval. .5m.sh = 5 min.
# SwiftBar features used: sfimage, color, font, size, symbolize

set -o pipefail

WIDGET_DIR="/Users/darrenneethling/Downloads/claude-usage-widget"
PYTHON="$WIDGET_DIR/.venv/bin/python"
FETCHER="$WIDGET_DIR/fetch_usage.py"
REFRESHER="$WIDGET_DIR/refresh_cookie.py"
CONFIG="$HOME/.claude-usage-widget.conf"
RAW="/tmp/claude-usage-raw.json"
ALERT_STATE="/tmp/claude-usage-alert-state"

# --- Thresholds for color coding ---------------------------------------------
WARN_PCT=60
CRIT_PCT=85

# --- Helpers -----------------------------------------------------------------
color_for_pct() {
  local pct="${1%%.*}"  # strip decimal
  [ -z "$pct" ] && { echo ""; return; }
  if [ "$pct" -ge "$CRIT_PCT" ]; then
    echo "#FF3B30"  # red
  elif [ "$pct" -ge "$WARN_PCT" ]; then
    echo "#FF9500"  # orange
  else
    echo "#34C759"  # green
  fi
}

# Unicode progress bar: ████████░░ style
progress_bar() {
  local pct="${1%%.*}"
  [ -z "$pct" ] && { echo ""; return; }
  local width=20
  local filled=$(( pct * width / 100 ))
  [ $filled -gt $width ] && filled=$width
  local empty=$(( width - filled ))
  local bar=""
  for ((i=0; i<filled; i++)); do bar+="█"; done
  for ((i=0; i<empty; i++)); do bar+="░"; done
  echo "$bar"
}

# Compact bar for title: ▓▓▓▓░░░░ (8 chars)
mini_bar() {
  local pct="${1%%.*}"
  [ -z "$pct" ] && { echo ""; return; }
  local width=8
  local filled=$(( pct * width / 100 ))
  [ $filled -gt $width ] && filled=$width
  local empty=$(( width - filled ))
  local bar=""
  for ((i=0; i<filled; i++)); do bar+="▓"; done
  for ((i=0; i<empty; i++)); do bar+="░"; done
  echo "$bar"
}

round() { [ -n "$1" ] && printf "%.0f" "$1" || echo ""; }

# ISO -> "in 1h 14m" or "Mon 3:00pm"
fmt_reset() {
  local iso="$1"
  [ -z "$iso" ] && return
  "$PYTHON" -c "
from datetime import datetime
try:
    s = '$iso'.replace('Z', '+00:00')
    if '.' in s:
        head, tail = s.split('.', 1)
        tz = ''
        for c in ('+', '-'):
            if c in tail:
                tz = c + tail.split(c, 1)[1]
                break
        s = head + tz
    dt = datetime.fromisoformat(s).astimezone()
    now = datetime.now(dt.tzinfo)
    total_min = int((dt - now).total_seconds() // 60)
    if total_min < 0: print('now')
    elif total_min < 60: print(f'in {total_min}m')
    elif total_min < 24*60:
        h, m = divmod(total_min, 60)
        print(f'in {h}h {m}m')
    else:
        print(dt.strftime('%a %-I:%M%p').lower())
except: pass
" 2>/dev/null
}

# macOS notification (only fires once per threshold crossing)
notify() {
  local title="$1" msg="$2" key="$3"
  # Check if already alerted
  if [ -f "$ALERT_STATE" ] && grep -q "$key" "$ALERT_STATE" 2>/dev/null; then
    return
  fi
  osascript -e "display notification \"$msg\" with title \"$title\" sound name \"Glass\"" 2>/dev/null
  echo "$key" >> "$ALERT_STATE"
}

# Reset alert state when usage drops (allows re-alerting next time it rises)
clear_alert() {
  local key="$1"
  [ -f "$ALERT_STATE" ] && sed -i '' "/$key/d" "$ALERT_STATE" 2>/dev/null
}

# --- Guards ------------------------------------------------------------------
if [ ! -x "$PYTHON" ] || [ ! -f "$FETCHER" ]; then
  echo "⚠ Setup | sfimage=exclamationmark.triangle color=#FF9500"
  echo "---"
  echo "Python venv or fetcher missing"
  echo "Expected: $PYTHON"
  echo "Expected: $FETCHER"
  exit 0
fi

if [ ! -f "$CONFIG" ]; then
  echo "⚠ Config | sfimage=exclamationmark.triangle color=#FF9500"
  echo "---"
  echo "Create config | bash='/usr/bin/open' param1='-t' param2='$CONFIG' terminal=false refresh=true"
  exit 0
fi

# --- Fetch (with auto-recovery) ----------------------------------------------
RESP=$("$PYTHON" "$FETCHER" 2>/tmp/claude-usage-err.log)
FETCH_EXIT=$?
echo "$RESP" > "$RAW"

# If fetch failed or got non-JSON, try auto-refreshing cookies once
if [ $FETCH_EXIT -ne 0 ] || [ -z "$RESP" ] || ! echo "$RESP" | jq -e . >/dev/null 2>&1; then
  if [ -x "$PYTHON" ] && [ -f "$REFRESHER" ]; then
    "$PYTHON" "$REFRESHER" 2>>/tmp/claude-usage-err.log
    if [ $? -eq 0 ]; then
      # Retry fetch with fresh cookies
      RESP=$("$PYTHON" "$FETCHER" 2>>/tmp/claude-usage-err.log)
      FETCH_EXIT=$?
      echo "$RESP" > "$RAW"
    fi
  fi
fi

# Still failing after recovery attempt
if [ $FETCH_EXIT -ne 0 ] || [ -z "$RESP" ] || ! echo "$RESP" | jq -e . >/dev/null 2>&1; then
  ERR=$(head -c 200 /tmp/claude-usage-err.log 2>/dev/null)
  echo "✖ Claude | sfimage=exclamationmark.circle color=#FF3B30"
  echo "---"
  echo "Fetch failed (auto cookie refresh also failed) | color=#FF3B30"
  echo "${ERR:-Unknown error} | size=11 font=Menlo color=#999999"
  echo "---"
  echo "Open Claude to refresh session | href=https://claude.ai/settings/usage"
  echo "Edit config | bash='/usr/bin/open' param1='-t' param2='$CONFIG' terminal=false"
  echo "View error log | bash='/usr/bin/open' param1='-t' param2='/tmp/claude-usage-err.log' terminal=false"
  exit 0
fi

# --- Parse -------------------------------------------------------------------
SESSION=$(echo "$RESP" | jq -r '.five_hour.utilization // empty')
SESSION_RESET=$(echo "$RESP" | jq -r '.five_hour.resets_at // empty')
WEEK=$(echo "$RESP" | jq -r '.seven_day.utilization // empty')
WEEK_RESET=$(echo "$RESP" | jq -r '.seven_day.resets_at // empty')
SONNET=$(echo "$RESP" | jq -r '.seven_day_sonnet.utilization // empty')
SONNET_RESET=$(echo "$RESP" | jq -r '.seven_day_sonnet.resets_at // empty')
OPUS=$(echo "$RESP" | jq -r '.seven_day_opus.utilization // empty')
OPUS_RESET=$(echo "$RESP" | jq -r '.seven_day_opus.resets_at // empty')
EXTRA_ENABLED=$(echo "$RESP" | jq -r '.extra_usage.is_enabled // "false"')
EXTRA_UTIL=$(echo "$RESP" | jq -r '.extra_usage.utilization // empty')

S_I=$(round "$SESSION")
W_I=$(round "$WEEK")

# --- Alerts ------------------------------------------------------------------
if [ -n "$S_I" ]; then
  if [ "$S_I" -ge "$CRIT_PCT" ]; then
    notify "Claude Usage" "Session at ${S_I}% — slow down or wait for reset" "session_crit"
  elif [ "$S_I" -ge "$WARN_PCT" ]; then
    notify "Claude Usage" "Session at ${S_I}% — approaching limit" "session_warn"
  else
    clear_alert "session_crit"
    clear_alert "session_warn"
  fi
fi

if [ -n "$W_I" ]; then
  if [ "$W_I" -ge "$CRIT_PCT" ]; then
    notify "Claude Usage" "Weekly at ${W_I}% — limit approaching, resets $(fmt_reset "$WEEK_RESET")" "weekly_crit"
  else
    clear_alert "weekly_crit"
  fi
fi

# --- Menu bar title ----------------------------------------------------------
# Determine dominant color from session (the one you hit most often)
TITLE_COLOR=$(color_for_pct "$S_I")

# Choose SF symbol based on session level
if [ -n "$S_I" ] && [ "$S_I" -ge "$CRIT_PCT" ]; then
  ICON="sfimage=bolt.trianglebadge.exclamationmark"
elif [ -n "$S_I" ] && [ "$S_I" -ge "$WARN_PCT" ]; then
  ICON="sfimage=gauge.with.dots.needle.67percent"
else
  ICON="sfimage=gauge.with.dots.needle.33percent"
fi

TITLE="${S_I:-0}%"
[ -n "$W_I" ] && TITLE="$TITLE · ${W_I}%w"
echo "$TITLE | $ICON color=$TITLE_COLOR size=12"

# --- Dropdown ----------------------------------------------------------------
echo "---"
echo "Claude Usage Dashboard | href=https://claude.ai/settings/usage size=14"
echo "---"

# Session
if [ -n "$SESSION" ]; then
  BAR=$(progress_bar "$S_I")
  CLR=$(color_for_pct "$S_I")
  RST=$(fmt_reset "$SESSION_RESET")
  echo "SESSION (5hr window) | size=11 color=#999999"
  echo "$BAR  ${SESSION}% | font=Menlo size=13 color=$CLR"
  [ -n "$RST" ] && echo "  Resets $RST | size=12"
  echo "---"
fi

# Weekly — all models
if [ -n "$WEEK" ]; then
  BAR=$(progress_bar "$W_I")
  CLR=$(color_for_pct "$W_I")
  RST=$(fmt_reset "$WEEK_RESET")
  echo "WEEKLY · ALL MODELS | size=11 color=#999999"
  echo "$BAR  ${WEEK}% | font=Menlo size=13 color=$CLR"
  [ -n "$RST" ] && echo "  Resets $RST | size=12"
  echo "---"
fi

# Weekly — Sonnet
if [ -n "$SONNET" ]; then
  SN_I=$(round "$SONNET")
  BAR=$(progress_bar "$SN_I")
  CLR=$(color_for_pct "$SN_I")
  RST=$(fmt_reset "$SONNET_RESET")
  echo "WEEKLY · SONNET | size=11 color=#999999"
  echo "$BAR  ${SONNET}% | font=Menlo size=13 color=$CLR"
  [ -n "$RST" ] && echo "  Resets $RST | size=12"
  echo "---"
fi

# Weekly — Opus (if present)
if [ -n "$OPUS" ]; then
  OP_I=$(round "$OPUS")
  BAR=$(progress_bar "$OP_I")
  CLR=$(color_for_pct "$OP_I")
  RST=$(fmt_reset "$OPUS_RESET")
  echo "WEEKLY · OPUS | size=11 color=#999999"
  echo "$BAR  ${OPUS}% | font=Menlo size=13 color=$CLR"
  [ -n "$RST" ] && echo "  Resets $RST | size=12"
  echo "---"
fi

# Extra usage / overages
if [ "$EXTRA_ENABLED" = "true" ]; then
  echo "EXTRA USAGE | size=11 color=#999999"
  if [ -n "$EXTRA_UTIL" ]; then
    EU_I=$(round "$EXTRA_UTIL")
    BAR=$(progress_bar "$EU_I")
    CLR=$(color_for_pct "$EU_I")
    echo "$BAR  ${EXTRA_UTIL}% | font=Menlo size=13 color=$CLR"
  else
    echo "  Enabled (no usage yet) | size=12 color=#34C759"
  fi
  echo "---"
fi

# Footer
echo "---"
echo "↻ Refresh now | refresh=true sfimage=arrow.clockwise"
echo "Open settings | href=https://claude.ai/settings/usage sfimage=gear"
echo "Edit config | bash='/usr/bin/open' param1='-t' param2='$CONFIG' terminal=false sfimage=pencil"
echo "View raw JSON | bash='/usr/bin/open' param1='-t' param2='$RAW' terminal=false sfimage=doc.text"
echo "View error log | bash='/usr/bin/open' param1='-t' param2='/tmp/claude-usage-err.log' terminal=false sfimage=exclamationmark.bubble"
echo "---"
echo "Force cookie refresh | bash='$PYTHON' param1='$REFRESHER' terminal=false refresh=true sfimage=key.fill"
