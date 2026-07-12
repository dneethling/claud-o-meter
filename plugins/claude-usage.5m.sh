#!/bin/bash
# <bitbar.title>Claude Usage</bitbar.title>
# <bitbar.version>v2.1</bitbar.version>
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
CC_USAGE="$WIDGET_DIR/claude_code_usage.py"
CC_SUMMARY="$HOME/.claude-usage-cc-summary.json"
CODEX_USAGE="$WIDGET_DIR/codex_usage.py"
CODEX_SUMMARY="$HOME/.claude-usage-codex-summary.json"
CONFIG="$HOME/.claude-usage-widget.conf"
RAW="/tmp/claude-usage-raw.json"
ERR_LOG="/tmp/claude-usage-err.log"
REFRESH_LOG="/tmp/claude-usage-refresh.log"
REFRESH_ERR="/tmp/claude-usage-refresh.err"
ALERT_STATE="/tmp/claude-usage-alert-state"
ALERT_LOCK="/tmp/claude-usage-alert.lock"
HISTORY_FILE="$HOME/.claude-usage-history"   # "epoch sessionPct weeklyPct" per render
HISTORY_CAP=288                              # 24h at 5-min cadence
LOG_MAX_BYTES=1048576    # 1 MB

# --- Log rotation ------------------------------------------------------------
# Rotate any oversized log to .1 and truncate. Cheap; runs every tick.
for f in "$ERR_LOG" "$REFRESH_LOG" "$REFRESH_ERR"; do
  if [ -f "$f" ]; then
    size=$(stat -f%z "$f" 2>/dev/null || echo 0)
    if [ "$size" -gt "$LOG_MAX_BYTES" ]; then
      mv -f "$f" "$f.1"
      : > "$f"
    fi
  fi
done

# --- Thresholds for color coding ---------------------------------------------
WARN_PCT=60
CRIT_PCT=85

# Pure display/formatting helpers (color_for_pct, progress_bar, round,
# humanize_tokens, humanize_usd, format_money, sparkline) live in lib/format.sh
# so they can be unit-tested. WIDGET_DIR is defined at the top of this file.
# shellcheck source=/dev/null
source "$WIDGET_DIR/lib/format.sh"

# --- Helpers -----------------------------------------------------------------
# Print one metric block: a single info line + a bar line, consistent sizing.
# Args: $1=label  $2=pct(raw)  $3=reset-text
print_metric() {
  local label="$1" pct="$2" reset="$3"
  [ -z "$pct" ] && return
  local pint clr bar info
  pint=$(round "$pct")
  clr=$(color_for_pct "$pint")
  bar=$(progress_bar "$pint")
  info="$label · ${pint}%"
  [ -n "$reset" ] && info="$info · resets $reset"
  # No color= on the text line → SwiftBar uses the adaptive system label
  # color (white in dark mode, black in light mode). Always readable.
  echo "$info | size=12"
  echo "$bar | font=Menlo size=12 color=$clr"
}

# Format every ISO timestamp passed as argv into a one-line "in 1h 14m" / "mon 3:00pm"
# label, in a SINGLE Python invocation with a 2s wall-clock timeout. Pass timestamps
# via argv (NOT string interpolation) so they can never break out of the Python source.
# Stdout is one line per input (empty line for empty input).
fmt_resets_all() {
  /usr/bin/timeout 2 "$PYTHON" - "$@" <<'PY' 2>/dev/null
import sys
from datetime import datetime

def fmt(iso):
    if not iso:
        return ""
    s = iso.replace("Z", "+00:00")
    if "." in s:
        head, tail = s.split(".", 1)
        tz = ""
        for c in ("+", "-"):
            if c in tail:
                tz = c + tail.split(c, 1)[1]
                break
        s = head + tz
    try:
        dt = datetime.fromisoformat(s).astimezone()
    except Exception:
        return ""
    now = datetime.now(dt.tzinfo)
    total_min = int((dt - now).total_seconds() // 60)
    if total_min < 0: return "now"
    if total_min < 60: return f"in {total_min}m"
    if total_min < 24*60:
        h, m = divmod(total_min, 60)
        return f"in {h}h {m}m"
    return dt.strftime("%a %-I:%M%p").lower()

for arg in sys.argv[1:]:
    print(fmt(arg))
PY
}

# macOS notification (only fires once per threshold crossing).
# Caller MUST hold the alert flock — see _under_alert_lock below.
notify() {
  local title="$1" msg="$2" key="$3"
  # Already-alerted check
  if [ -f "$ALERT_STATE" ] && grep -F -x -q "$key" "$ALERT_STATE" 2>/dev/null; then
    return
  fi
  # Escape any double-quotes in title/msg before injecting into AppleScript
  local s_title="${title//\"/\\\"}"
  local s_msg="${msg//\"/\\\"}"
  osascript -e "display notification \"$s_msg\" with title \"$s_title\" sound name \"Glass\"" 2>/dev/null
  echo "$key" >> "$ALERT_STATE"
}

# Reset alert state when usage drops (allows re-alerting next time it rises).
# Atomic: rewrite a temp file with the key removed, then rename. Uses grep -F -x
# so a key containing regex meta-characters can't accidentally over-match.
clear_alert() {
  local key="$1"
  [ -f "$ALERT_STATE" ] || return 0
  local tmp="${ALERT_STATE}.tmp.$$"
  if grep -F -x -v -- "$key" "$ALERT_STATE" > "$tmp" 2>/dev/null; then
    mv -f "$tmp" "$ALERT_STATE"
  else
    # grep returns 1 when nothing matches (file becomes empty) — still rename
    if [ -f "$tmp" ]; then mv -f "$tmp" "$ALERT_STATE"; fi
  fi
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
# fetch_usage.py now writes empty stdout on any failure and writes the reason
# to stderr. So $RESP is either a valid JSON body or empty.
RESP=$("$PYTHON" "$FETCHER" 2>"$ERR_LOG")
FETCH_EXIT=$?

REFRESH_FAILED=0
if [ $FETCH_EXIT -ne 0 ] || [ -z "$RESP" ]; then
  if [ -f "$REFRESHER" ]; then
    "$PYTHON" "$REFRESHER" 2>>"$ERR_LOG"
    REFRESH_RC=$?
    if [ $REFRESH_RC -eq 0 ]; then
      RESP=$("$PYTHON" "$FETCHER" 2>>"$ERR_LOG")
      FETCH_EXIT=$?
    else
      REFRESH_FAILED=1
    fi
  fi
fi

# Still failing after recovery attempt → distinguish reauth-needed from transient errors
if [ $FETCH_EXIT -ne 0 ] || [ -z "$RESP" ]; then
  ERR=$(tail -c 240 "$ERR_LOG" 2>/dev/null | tr '\n' ' ')
  if [ $REFRESH_FAILED -eq 1 ] || echo "$ERR" | grep -q "account_session_invalid\|No valid Claude session"; then
    # Genuinely needs the user to re-login in a browser
    echo "⚠ Re-auth | sfimage=person.badge.key color=#FF9500"
    echo "---"
    echo "Session expired — log into claude.ai in your browser"
    echo "(Arc, Chrome, or Brave). Then the widget will recover automatically."
    echo "---"
    echo "Open claude.ai | href=https://claude.ai/login sfimage=safari"
    echo "Force cookie refresh | bash='$PYTHON' param1='$REFRESHER' terminal=false refresh=true sfimage=key.fill"
  else
    # Transient — network, Cloudflare, etc.
    echo "✖ Claude | sfimage=exclamationmark.circle color=#FF3B30"
    echo "---"
    echo "Fetch failed | color=#FF3B30"
    echo "${ERR:-Unknown error} | size=11 font=Menlo color=#999999"
    echo "---"
    echo "Open Claude | href=https://claude.ai/settings/usage"
  fi
  echo "Edit config | bash='/usr/bin/open' param1='-t' param2='$CONFIG' terminal=false sfimage=pencil"
  echo "View error log | bash='/usr/bin/open' param1='-t' param2='$ERR_LOG' terminal=false sfimage=exclamationmark.bubble"
  exit 0
fi

# Got a valid response — only NOW save it as the canonical raw JSON
echo "$RESP" > "$RAW"

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

# Usage credits / spend block (paid overage when weekly is exhausted).
# Use .spend.* as the canonical source; it's richer than .extra_usage.
SPEND_ENABLED=$(echo "$RESP" | jq -r '.spend.enabled // false')
SPEND_USED=$(echo "$RESP"     | jq -r '.spend.used.amount_minor // empty')
SPEND_LIMIT=$(echo "$RESP"    | jq -r '.spend.limit.amount_minor // empty')
SPEND_CURRENCY=$(echo "$RESP" | jq -r '.spend.used.currency // empty')
SPEND_EXPONENT=$(echo "$RESP" | jq -r '.spend.used.exponent // 2')
SPEND_PCT=$(echo "$RESP"      | jq -r '.spend.percent // empty')

S_I=$(round "$SESSION")
W_I=$(round "$WEEK")
SPEND_I=$(round "$SPEND_PCT")

# "On credits" when weekly is exhausted AND credit spending is active.
# This is the trigger to switch the menu-bar headline from "%w" to "$X.XX".
ON_CREDITS=0
if [[ "$W_I" =~ ^[0-9]+$ ]] && [ "$W_I" -ge 100 ] \
   && [ "$SPEND_ENABLED" = "true" ] \
   && [ -n "$SPEND_USED" ] && [ "$SPEND_USED" != "null" ]; then
  ON_CREDITS=1
fi

# Pre-format the spend amounts for reuse below
SPEND_USED_STR=$(format_money "$SPEND_USED" "$SPEND_CURRENCY" "$SPEND_EXPONENT")
SPEND_LIMIT_STR=$(format_money "$SPEND_LIMIT" "$SPEND_CURRENCY" "$SPEND_EXPONENT")

# Detect API-shape change: 200 + JSON but our keys are gone.
SHAPE_BROKEN=0
if [ -z "$S_I" ] && [ -z "$W_I" ]; then
  SHAPE_BROKEN=1
fi

# Append a history sample and trim (used for the sparkline). Numeric-only so a
# bad tick can never pollute the trend.
if [[ "$S_I" =~ ^[0-9]+$ ]] && [[ "$W_I" =~ ^[0-9]+$ ]]; then
  printf '%s %s %s\n' "$(date +%s)" "$S_I" "$W_I" >> "$HISTORY_FILE"
  hlines=$(wc -l < "$HISTORY_FILE" 2>/dev/null | tr -d ' ')
  if [ -n "$hlines" ] && [ "$hlines" -gt "$HISTORY_CAP" ]; then
    tail -n "$HISTORY_CAP" "$HISTORY_FILE" > "$HISTORY_FILE.tmp" && mv -f "$HISTORY_FILE.tmp" "$HISTORY_FILE"
  fi
fi

# Pre-compute all four reset strings in ONE Python invocation.
# bash 3.2 (macOS default) has no `mapfile` — use a portable while-read.
RESET_LINES=()
while IFS= read -r _line; do RESET_LINES+=("$_line"); done < <(fmt_resets_all "$SESSION_RESET" "$WEEK_RESET" "$SONNET_RESET" "$OPUS_RESET")
SESSION_RESET_TXT="${RESET_LINES[0]:-}"
WEEK_RESET_TXT="${RESET_LINES[1]:-}"
SONNET_RESET_TXT="${RESET_LINES[2]:-}"
OPUS_RESET_TXT="${RESET_LINES[3]:-}"

# --- Alerts ------------------------------------------------------------------
# macOS has no `flock` util — use the atomic-mkdir pattern as the mutex.
# Only fire when S_I/W_I are real integers; otherwise `-ge` would error.
ALERT_LOCK_DIR="${ALERT_LOCK}.d"
if mkdir "$ALERT_LOCK_DIR" 2>/dev/null; then
  trap 'rmdir "$ALERT_LOCK_DIR" 2>/dev/null' EXIT INT TERM
  if [[ "$S_I" =~ ^[0-9]+$ ]]; then
    if [ "$S_I" -ge "$CRIT_PCT" ]; then
      notify "Claude Usage" "Session at ${S_I}% — slow down or wait for reset" "session_crit"
    elif [ "$S_I" -ge "$WARN_PCT" ]; then
      notify "Claude Usage" "Session at ${S_I}% — approaching limit" "session_warn"
    else
      clear_alert "session_crit"
      clear_alert "session_warn"
    fi
  fi

  if [[ "$W_I" =~ ^[0-9]+$ ]]; then
    if [ "$W_I" -ge 100 ]; then
      # Single one-time notice when the weekly limit actually hits 100%.
      # Distinct key from `weekly_crit` so users still get the 85% heads-up.
      if [ $ON_CREDITS -eq 1 ]; then
        notify "Claude Usage" "Weekly limit hit — now on usage credits (${SPEND_USED_STR} of ${SPEND_LIMIT_STR})" "weekly_hit_100"
      else
        notify "Claude Usage" "Weekly limit hit — credits disabled, you may be blocked" "weekly_hit_100"
      fi
    elif [ "$W_I" -ge "$CRIT_PCT" ]; then
      notify "Claude Usage" "Weekly at ${W_I}% — limit approaching, resets ${WEEK_RESET_TXT}" "weekly_crit"
      clear_alert "weekly_hit_100"
    else
      clear_alert "weekly_crit"
      clear_alert "weekly_hit_100"
    fi
  fi

  # Usage credit alerts — only fire when we're actually on credits.
  if [ $ON_CREDITS -eq 1 ] && [[ "$SPEND_I" =~ ^[0-9]+$ ]]; then
    if [ "$SPEND_I" -ge "$CRIT_PCT" ]; then
      notify "Claude Usage" "Credits at ${SPEND_I}% (${SPEND_USED_STR} of ${SPEND_LIMIT_STR}) — top up or wait for reset" "credits_crit"
    elif [ "$SPEND_I" -ge "$WARN_PCT" ]; then
      notify "Claude Usage" "Credits at ${SPEND_I}% — approaching monthly cap" "credits_warn"
    else
      clear_alert "credits_crit"
      clear_alert "credits_warn"
    fi
  else
    clear_alert "credits_crit"
    clear_alert "credits_warn"
  fi
fi
# If mkdir failed another run is in the alerts section — skip alerts this tick.

# --- Menu bar title ----------------------------------------------------------
if [ $SHAPE_BROKEN -eq 1 ]; then
  # Successful fetch but extraction empty — surface loudly, don't show fake "0%"
  echo "?% | sfimage=questionmark.circle color=#FF9500 size=12"
  echo "---"
  echo "Claude Usage Dashboard | href=https://claude.ai/settings/usage size=14"
  echo "---"
  echo "API shape may have changed | color=#FF9500"
  echo "Open the raw JSON below — if Anthropic renamed a key, paste the snippet"
  echo "to the maintainer so the plugin can be updated."
  echo "---"
  echo "View raw JSON | bash='/usr/bin/open' param1='-t' param2='$RAW' terminal=false sfimage=doc.text"
  echo "↻ Refresh now | refresh=true sfimage=arrow.clockwise"
  echo "Edit config | bash='/usr/bin/open' param1='-t' param2='$CONFIG' terminal=false sfimage=pencil"
  echo "Quit (until next interval) | href=https://claude.ai/settings/usage"
  exit 0
fi

# Menu bar icon + title
# When the weekly limit is exhausted and credits are paying for usage,
# switch the headline from "%w" to the actual credit spend "$X.XX".
if [ $ON_CREDITS -eq 1 ]; then
  ICON="sfimage=creditcard.fill"
  # Title colour reflects credit-pool fill, not session — credits are the active scarcity now
  TITLE_COLOR=$(color_for_pct "$SPEND_I")
  TITLE="${S_I:-?}% · ${SPEND_USED_STR}"
else
  if [[ "$S_I" =~ ^[0-9]+$ ]] && [ "$S_I" -ge "$CRIT_PCT" ]; then
    ICON="sfimage=bolt.trianglebadge.exclamationmark"
  elif [[ "$S_I" =~ ^[0-9]+$ ]] && [ "$S_I" -ge "$WARN_PCT" ]; then
    ICON="sfimage=gauge.with.dots.needle.67percent"
  else
    ICON="sfimage=gauge.with.dots.needle.33percent"
  fi
  TITLE_COLOR=$(color_for_pct "$S_I")
  TITLE="${S_I:-?}%"
  if [[ "$W_I" =~ ^[0-9]+$ ]]; then
    TITLE="$TITLE · ${W_I}%w"
  fi
fi
echo "$TITLE | $ICON color=$TITLE_COLOR size=12"

# --- Dropdown ----------------------------------------------------------------
echo "---"
echo "Claude Usage Dashboard | href=https://claude.ai/settings/usage size=14"
echo "---"

# Credits block: only when actually on credits — shown FIRST since it's what
# the user is actively burning down right now.
if [ $ON_CREDITS -eq 1 ]; then
  SPEND_CLR=$(color_for_pct "$SPEND_I")
  SPEND_BAR=$(progress_bar "$SPEND_I")
  echo "On usage credits · ${SPEND_USED_STR} of ${SPEND_LIMIT_STR} (${SPEND_I}%) | size=12 color=$SPEND_CLR"
  echo "$SPEND_BAR | font=Menlo size=12 color=$SPEND_CLR"
  echo "  Weekly limit reached — Claude is billing against your credit pool until ${WEEK_RESET_TXT:-reset}. | size=11 color=#8E8E93"
  echo "---"
fi

if [ -n "$SESSION" ]; then
  print_metric "Session · 5h" "$SESSION" "$SESSION_RESET_TXT"
  if [ -f "$HISTORY_FILE" ]; then
    SESSION_TREND=$(tail -n 24 "$HISTORY_FILE" 2>/dev/null | awk '{printf "%s ", $2}')
    SPARK=$(sparkline "$SESSION_TREND")
    [ -n "$SPARK" ] && echo "  trend (last ~2h) $SPARK | font=Menlo size=11 color=#8E8E93"
  fi
  echo "---"
fi

if [ -n "$WEEK" ]; then
  if [ $ON_CREDITS -eq 1 ]; then
    # Weekly is exhausted — render it as 100% red with an "exhausted" tag instead of a generic bar.
    echo "Weekly · all models · 100% · exhausted, resets ${WEEK_RESET_TXT:-?} | size=12 color=#FF3B30"
    echo "$(progress_bar 100) | font=Menlo size=12 color=#FF3B30"
    echo "---"
  else
    print_metric "Weekly · all models" "$WEEK" "$WEEK_RESET_TXT"
    echo "---"
  fi
fi

if [ -n "$SONNET" ]; then
  print_metric "Weekly · Sonnet" "$SONNET" "$SONNET_RESET_TXT"
  echo "---"
fi

if [ -n "$OPUS" ]; then
  print_metric "Weekly · Opus" "$OPUS" "$OPUS_RESET_TXT"
  echo "---"
fi

# Extra usage block — only show when credits are enabled but NOT actively in use,
# so we don't render the credit pool twice (the top "On usage credits" block
# already covers the active case).
if [ "$EXTRA_ENABLED" = "true" ] && [ $ON_CREDITS -eq 0 ]; then
  if [ -n "$SPEND_USED" ] && [ "$SPEND_USED" != "null" ] && [ -n "$SPEND_LIMIT" ]; then
    echo "Credits available · ${SPEND_USED_STR} of ${SPEND_LIMIT_STR} used this month | size=12"
  else
    echo "Credits enabled · ready when weekly limit hits | size=12"
  fi
  echo "---"
fi

# --- Claude Code (local token usage) -----------------------------------------
# Read straight from ~/.claude logs — 100% local, no cookies, never expires.
# Call the parser with a short timeout; on timeout fall back to the warm summary
# file so a cold cache never freezes the menu (parser keeps that file updated).
CC_JSON=$(/usr/bin/timeout 4 "$PYTHON" "$CC_USAGE" 2>/dev/null)
if [ -z "$CC_JSON" ] && [ -f "$CC_SUMMARY" ]; then
  CC_JSON=$(cat "$CC_SUMMARY")
fi

if [ -n "$CC_JSON" ] && echo "$CC_JSON" | jq -e . >/dev/null 2>&1; then
  CC_TODAY_TOK=$(echo "$CC_JSON" | jq -r '.today.total_tokens // 0')
  CC_TODAY_USD=$(echo "$CC_JSON" | jq -r '.today.est_cost_usd // 0')
  CC_WEEK_TOK=$(echo "$CC_JSON"  | jq -r '.week.total_tokens // 0')
  CC_WEEK_USD=$(echo "$CC_JSON"  | jq -r '.week.est_cost_usd // 0')
  CC_MONTH_TOK=$(echo "$CC_JSON" | jq -r '.month.total_tokens // 0')
  CC_MONTH_USD=$(echo "$CC_JSON" | jq -r '.month.est_cost_usd // 0')

  echo "CLAUDE CODE · local, this machine | size=11 color=#8E8E93"
  echo "Today · $(humanize_tokens "$CC_TODAY_TOK") tokens · ≈$(humanize_usd "$CC_TODAY_USD") value | size=12"
  echo "7 days · $(humanize_tokens "$CC_WEEK_TOK") · 30 days · $(humanize_tokens "$CC_MONTH_TOK") | size=11 color=#8E8E93"
  echo "Value extracted from Max: ≈$(humanize_usd "$CC_MONTH_USD")/mo at API rates | size=11 color=#8E8E93"
  echo "---"
fi

# --- Codex (local token usage) -----------------------------------------------
# Read straight from ~/.codex/state_5.sqlite (read-only). Flat-rate ChatGPT
# account, so token volume is the honest metric — no per-token bill.
CODEX_JSON=$(/usr/bin/timeout 3 "$PYTHON" "$CODEX_USAGE" 2>/dev/null)
if [ -z "$CODEX_JSON" ] && [ -f "$CODEX_SUMMARY" ]; then
  CODEX_JSON=$(cat "$CODEX_SUMMARY")
fi

if [ -n "$CODEX_JSON" ] && echo "$CODEX_JSON" | jq -e '.available == true' >/dev/null 2>&1; then
  CX_TODAY_TOK=$(echo "$CODEX_JSON" | jq -r '.today.tokens // 0')
  CX_TODAY_THR=$(echo "$CODEX_JSON" | jq -r '.today.threads // 0')
  CX_WEEK_TOK=$(echo "$CODEX_JSON"  | jq -r '.week.tokens // 0')
  CX_MONTH_TOK=$(echo "$CODEX_JSON" | jq -r '.month.tokens // 0')
  CX_ALL_TOK=$(echo "$CODEX_JSON"   | jq -r '.all_time.tokens // 0')

  CX_THR_LABEL="threads"; [ "$CX_TODAY_THR" = "1" ] && CX_THR_LABEL="thread"
  echo "CODEX · local, this machine | size=11 color=#8E8E93"
  echo "Today · $(humanize_tokens "$CX_TODAY_TOK") tokens · ${CX_TODAY_THR} ${CX_THR_LABEL} | size=12"
  echo "7 days · $(humanize_tokens "$CX_WEEK_TOK") · 30 days · $(humanize_tokens "$CX_MONTH_TOK") · all-time · $(humanize_tokens "$CX_ALL_TOK") | size=11 color=#8E8E93"
  echo "---"
fi

# Footer
echo "---"
echo "↻ Refresh now | refresh=true sfimage=arrow.clockwise"
echo "Open settings | href=https://claude.ai/settings/usage sfimage=gear"
echo "Edit config | bash='/usr/bin/open' param1='-t' param2='$CONFIG' terminal=false sfimage=pencil"
echo "View raw JSON | bash='/usr/bin/open' param1='-t' param2='$RAW' terminal=false sfimage=doc.text"
echo "View error log | bash='/usr/bin/open' param1='-t' param2='$ERR_LOG' terminal=false sfimage=exclamationmark.bubble"
echo "---"
echo "Force cookie refresh | bash='$PYTHON' param1='$REFRESHER' terminal=false refresh=true sfimage=key.fill"
