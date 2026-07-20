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

# Portable: derive the repo root from this script's own location, so the widget
# works on any machine/username (SwiftBar runs the plugin from <repo>/plugins/).
# Falls back to the env override CLAUDE_WIDGET_DIR if set.
WIDGET_DIR="${CLAUDE_WIDGET_DIR:-$(cd "$(dirname "${BASH_SOURCE[0]:-$0}")/.." 2>/dev/null && pwd)}"
PYTHON="$WIDGET_DIR/.venv/bin/python"
FETCHER="$WIDGET_DIR/fetch_usage.py"
REFRESHER="$WIDGET_DIR/refresh_cookie.py"
CC_USAGE="$WIDGET_DIR/claude_code_usage.py"
CC_SUMMARY="$HOME/.claude-usage-cc-summary.json"
CODEX_USAGE="$WIDGET_DIR/codex_usage.py"
CODEX_SUMMARY="$HOME/.claude-usage-codex-summary.json"
STATUS_CHECK="$WIDGET_DIR/status_check.py"
PREDICT="$WIDGET_DIR/predict.py"
CONFIG="$HOME/.claude-usage-widget.conf"
RAW="/tmp/claude-usage-raw.json"
ERR_LOG="/tmp/claude-usage-err.log"
REFRESH_LOG="/tmp/claude-usage-refresh.log"
REFRESH_ERR="/tmp/claude-usage-refresh.err"
ALERT_STATE="/tmp/claude-usage-alert-state"
ALERT_LOCK="/tmp/claude-usage-alert.lock"
MUTE_FILE="$HOME/.claude-usage-mute-until"   # epoch; alerts suppressed until then
LASTSEEN_FILE="$HOME/.claude-usage-lastseen" # "session weekly" from the previous tick
COPY_SUMMARY="$WIDGET_DIR/copy_summary.py"
EXPORT="$WIDGET_DIR/export_usage.py"
CHECK_UPDATE="$WIDGET_DIR/check_update.sh"
UPDATE_SCRIPT="$WIDGET_DIR/update.sh"
UPDATE_STATUS="$HOME/.claude-usage-update-status"
UPDATE_CHECK_INTERVAL=21600   # re-check GitHub at most every 6h (in background)
REPO_URL="https://github.com/dneethling/claud-o-meter"
RESET_DROP=30                                # a fall of this many points = a reset (clear-to-go ping)
HISTORY_FILE="$HOME/.claude-usage-history"   # "epoch sessionPct weeklyPct" per render
HISTORY_CAP=2016                             # 7 days at 5-min cadence (for weekly prediction)
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

# Readable label colour for informational (non-clickable) menu lines. SwiftBar
# dims non-actionable items, and a bare adaptive colour does not override that,
# so we force full contrast. Detect the system appearance and pick a single,
# definitely-supported colour (avoids relying on SwiftBar light,dark comma
# syntax, which if unsupported would render near-black on a dark menu).
if defaults read -g AppleInterfaceStyle 2>/dev/null | grep -qi dark; then
  LBL="#f2f2f7"   # near-white for dark appearance
  DARKJSON="true" # drawn meters need a lighter track on a dark menu
  SEC_CC="#8F8CFF"  # Claude Code section: indigo
  SEC_CX="#3ED9D3"  # Codex section: teal
else
  LBL="#1c1c1e"   # near-black for light appearance
  DARKJSON="false"
  SEC_CC="#5E5CE6"
  SEC_CX="#0E9F9A"
fi
# Section hues are deliberately clear of the semantic green/orange/red used for
# gauge state, so a coloured heading can never be misread as a status. Each
# section's sparkline is drawn in its own hue, which is what ties a graph to the
# heading above it.

# Menu-bar title mode. Override by adding a MENUBAR_MODE=... line to the config.
#   claude (default) -> "16% · 10%w"
#   codex            -> "16% · 10%w · cx 120M"          (adds Codex 30-day tokens)
#   both             -> "16% · 10%w · cc 20.5B · cx 120M" (adds Claude Code too)
MENUBAR_MODE=$(grep -E '^MENUBAR_MODE=' "$CONFIG" 2>/dev/null | head -1 | cut -d= -f2 | tr -d ' ')
[ -z "$MENUBAR_MODE" ] && MENUBAR_MODE="claude"

# Colour theme: semantic (default) | minimal | colorblind. Read from config,
# exported so lib/format.sh color_for_pct sees it.
THEME=$(grep -E '^THEME=' "$CONFIG" 2>/dev/null | head -1 | cut -d= -f2 | tr -d ' ')
[ -z "$THEME" ] && THEME="semantic"
export THEME

# Drawn meters (real PNGs) instead of ASCII bars. Add GRAPHICS=0 to the config to
# fall back to text bars. Rendering is pure-Python with no dependencies, and every
# failure path falls back to the ASCII bar, so this can never blank a gauge.
GRAPHICS=$(grep -E '^GRAPHICS=' "$CONFIG" 2>/dev/null | head -1 | cut -d= -f2 | tr -d ' ')
[ -z "$GRAPHICS" ] && GRAPHICS="1"
METER_CACHE="$HOME/.cache/claude-usage-widget/meters"
[ "$GRAPHICS" = "1" ] && mkdir -p "$METER_CACHE" 2>/dev/null

# Base64 PNG for one meter, cached on disk by (appearance, colour, percent).
# Percentages are integers 0-100 so the cache saturates almost immediately and
# steady-state refreshes do no rendering work at all.
meter_b64() {
  local pint="$1" clr="$2" f tmp frac img
  [ "$GRAPHICS" = "1" ] || return 0
  [ -x "$PYTHON" ] || return 0
  f="$METER_CACHE/m-${DARKJSON}-${clr#\#}-${pint}.b64"
  if [ ! -s "$f" ]; then
    frac=$(awk -v p="$pint" 'BEGIN{printf "%.4f", p/100}')
    # PID-unique temp: two refreshes rendering the same key must not delete
    # each other's in-progress file. Rename is atomic, so readers see all-or-nothing.
    tmp="$f.$$.tmp"
    printf '[{"key":"m","type":"meter","frac":%s,"color":"%s","dark":%s,"w":54,"h":7}]' \
      "$frac" "$clr" "$DARKJSON" \
      | run_timeout 6 "$PYTHON" "$WIDGET_DIR/render_assets.py" 2>/dev/null \
      | awk -F'\t' 'NR==1{print $2}' > "$tmp" 2>/dev/null
    if [ -s "$tmp" ]; then mv -f "$tmp" "$f" 2>/dev/null; else rm -f "$tmp" 2>/dev/null; fi
  fi
  [ -s "$f" ] || return 0
  img=$(cat "$f" 2>/dev/null)
  # Never trust the cache blindly: a truncated, hand-edited or disk-corrupted
  # file containing a space, "|" or newline would break SwiftBar's
  # "text | key=value" row protocol and mangle the whole menu. Emit only a
  # single run of strict base64 of a sane length; anything else falls back to
  # the ASCII bar.
  case "$img" in
    ""|*[!A-Za-z0-9+/=]*) return 0 ;;
  esac
  [ ${#img} -gt 50000 ] && return 0
  printf '%s' "$img"
}

# Base64 PNG for the menu-bar ring gauge. Same bounded cache as meters (101
# percentages x colour x appearance), so it renders once and is then free.
ring_b64() {
  local pint="$1" clr="$2" f tmp frac img
  [ "$GRAPHICS" = "1" ] || return 0
  [ -x "$PYTHON" ] || return 0
  f="$METER_CACHE/r-${DARKJSON}-${clr#\#}-${pint}.b64"
  if [ ! -s "$f" ]; then
    frac=$(awk -v p="$pint" 'BEGIN{printf "%.4f", p/100}')
    tmp="$f.$$.tmp"
    printf '[{"key":"r","type":"ring","frac":%s,"color":"%s","dark":%s,"d":14,"thick":2.6}]' \
      "$frac" "$clr" "$DARKJSON" \
      | run_timeout 6 "$PYTHON" "$WIDGET_DIR/render_assets.py" 2>/dev/null \
      | awk -F'\t' 'NR==1{print $2}' > "$tmp" 2>/dev/null
    if [ -s "$tmp" ]; then mv -f "$tmp" "$f" 2>/dev/null; else rm -f "$tmp" 2>/dev/null; fi
  fi
  [ -s "$f" ] || return 0
  img=$(cat "$f" 2>/dev/null)
  case "$img" in
    ""|*[!A-Za-z0-9+/=]*) return 0 ;;
  esac
  [ ${#img} -gt 50000 ] && return 0
  printf '%s' "$img"
}

# Base64 PNG for a sparkline, keyed by a hash of its own values. Unlike meters
# (a bounded set of 101 percentages) trend data drifts every refresh, so these
# entries would accumulate - prune anything untouched for 3 days on each run.
[ "$GRAPHICS" = "1" ] && find "$METER_CACHE" -name 's-*.b64' -mtime +3 -delete 2>/dev/null
spark_b64() {
  local vals="$1" clr="$2" f tmp json key img
  [ "$GRAPHICS" = "1" ] || return 0
  [ -x "$PYTHON" ] || return 0
  [ -z "$vals" ] && return 0
  key=$(printf '%s' "${vals}${clr}${DARKJSON}" | shasum 2>/dev/null | cut -c1-16)
  [ -z "$key" ] && return 0
  f="$METER_CACHE/s-$key.b64"
  if [ ! -s "$f" ]; then
    # space-separated numbers -> JSON array. Anything non-numeric coerces to 0,
    # and non-finite values are forced to 0 too: awk turns "1e999" into "inf"
    # and "NaN" into "nan", neither of which is valid JSON, which would make the
    # renderer reject the whole spec.
    json=$(printf '%s' "$vals" | awk '{
      for (i=1;i<=NF;i++) {
        v = $i + 0
        if (v != v || v == v + 1) v = 0   # NaN, +inf, -inf
        printf (i>1 ? "," : "") "%.6g", v
      }
    }')
    [ -z "$json" ] && return 0
    tmp="$f.$$.tmp"
    printf '[{"key":"s","type":"spark","values":[%s],"color":"%s","dark":%s,"w":104,"h":15}]' \
      "$json" "$clr" "$DARKJSON" \
      | run_timeout 6 "$PYTHON" "$WIDGET_DIR/render_assets.py" 2>/dev/null \
      | awk -F'\t' 'NR==1{print $2}' > "$tmp" 2>/dev/null
    if [ -s "$tmp" ]; then mv -f "$tmp" "$f" 2>/dev/null; else rm -f "$tmp" 2>/dev/null; fi
  fi
  [ -s "$f" ] || return 0
  img=$(cat "$f" 2>/dev/null)
  case "$img" in
    ""|*[!A-Za-z0-9+/=]*) return 0 ;;
  esac
  [ ${#img} -gt 50000 ] && return 0
  printf '%s' "$img"
}

# Pure display/formatting helpers (color_for_pct, progress_bar, round,
# humanize_tokens, humanize_usd, format_money, sparkline) live in lib/format.sh
# so they can be unit-tested. WIDGET_DIR is defined at the top of this file.
# shellcheck source=/dev/null
source "$WIDGET_DIR/lib/format.sh"

# Portable timeout: macOS ships no `timeout`/`gtimeout`. Runs a command and
# kills it (SIGTERM) if it exceeds N seconds. Uses perl (always present on
# macOS); exec preserves stdin/stdout/stderr, so heredoc-fed callers work.
run_timeout() {
  local secs="$1"; shift
  perl -e '
    my $t = shift;
    my $pid = fork();
    if (!defined $pid) { exit 127 }
    if ($pid == 0) { exec @ARGV; exit 127 }
    local $SIG{ALRM} = sub { kill "TERM", $pid; };
    alarm $t;
    waitpid($pid, 0);
    my $rc = $? >> 8;
    alarm 0;
    exit $rc;
  ' "$secs" "$@"
}

# --- Helpers -----------------------------------------------------------------
# Print one metric block: a single info line + a bar line, consistent sizing.
# Args: $1=label  $2=pct(raw)  $3=reset-text
print_metric() {
  local label="$1" pct="$2" reset="$3"
  [ -z "$pct" ] && return
  local pint clr bar info img
  pint=$(round "$pct")
  clr=$(color_for_pct "$pint")
  info="$label · ${pint}%"
  [ -n "$reset" ] && info="$info · resets $reset"
  # A drawn meter rides on the metric row itself (SwiftBar renders a row image
  # leading the text), collapsing what used to be a text row plus an ASCII bar
  # row into one. If rendering is off or fails for any reason we fall back to
  # the original two-row text form, so a gauge is never lost.
  img=$(meter_b64 "$pint" "$clr")
  if [ -n "$img" ]; then
    echo "$info | size=12 color=$LBL image=$img"
  else
    bar=$(progress_bar "$pint")
    echo "$info | size=12 color=$LBL"
    echo "$bar | font=Menlo size=12 $(colorkey "$clr")"
  fi
}

# Format every ISO timestamp passed as argv into a one-line "in 1h 14m" / "mon 3:00pm"
# label, in a SINGLE Python invocation with a 2s wall-clock timeout. Pass timestamps
# via argv (NOT string interpolation) so they can never break out of the Python source.
# Stdout is one line per input (empty line for empty input).
fmt_resets_all() {
  run_timeout 2 "$PYTHON" - "$@" <<'PY' 2>/dev/null
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
    # >=24h out: weekday + time alone is ambiguous (this week vs next - a reset
    # 7 days away on a Wednesday looks identical to one earlier today). Include
    # the calendar date so it is unmistakable. e.g. "wed 22 jul, 9:03am".
    return dt.strftime("%a %-d %b, %-I:%M%p").lower()

for arg in sys.argv[1:]:
    print(fmt(arg))
PY
}

# macOS notification (only fires once per threshold crossing).
# Caller MUST hold the alert flock — see _under_alert_lock below.
notify() {
  local title="$1" msg="$2" key="$3"
  # Snooze: suppress all alerts while a mute is active.
  if [ -f "$MUTE_FILE" ]; then
    local until; until=$(cat "$MUTE_FILE" 2>/dev/null)
    if [ -n "$until" ] && [ "$(date +%s)" -lt "$until" ] 2>/dev/null; then
      return
    fi
  fi
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
# Per-model weekly limits (Fable / Sonnet / Opus / ...). The authoritative source
# is the limits[] array, which carries the human-readable model name; the old
# top-level seven_day_sonnet/opus fields are often null now. One tab-separated
# line per scoped model: "name<TAB>percent<TAB>resets_at".
SCOPED_LIMITS=$(echo "$RESP" | jq -r '.limits[]? | select(.scope.model.display_name != null) | [.scope.model.display_name, (.percent|tostring), (.resets_at // "")] | @tsv' 2>/dev/null)
# Fallback to the legacy fields if limits[] carried no scoped entries.
if [ -z "$SCOPED_LIMITS" ]; then
  _sv=$(echo "$RESP" | jq -r '.seven_day_sonnet.utilization // empty')
  _sr=$(echo "$RESP" | jq -r '.seven_day_sonnet.resets_at // empty')
  _ov=$(echo "$RESP" | jq -r '.seven_day_opus.utilization // empty')
  _or=$(echo "$RESP" | jq -r '.seven_day_opus.resets_at // empty')
  [ -n "$_sv" ] && SCOPED_LIMITS="$(printf 'Sonnet\t%s\t%s' "$_sv" "$_sr")"
  [ -n "$_ov" ] && SCOPED_LIMITS="$(printf '%s\nOpus\t%s\t%s' "$SCOPED_LIMITS" "$_ov" "$_or")"
fi
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

# Prediction: burn-rate ETA vs reset time (reads the history just written above).
PRED_WEEK_VERDICT=""; PRED_WEEK_ETA=""
PRED_JSON=$(run_timeout 3 "$PYTHON" "$PREDICT" "$SESSION_RESET" "$WEEK_RESET" 2>/dev/null)
if [ -n "$PRED_JSON" ] && echo "$PRED_JSON" | jq -e . >/dev/null 2>&1; then
  PRED_WEEK_VERDICT=$(echo "$PRED_JSON" | jq -r '.weekly.verdict // ""')
  PRED_WEEK_ETA=$(echo "$PRED_JSON" | jq -r '.weekly.eta_iso // ""')
fi

# Pre-compute all four reset strings in ONE Python invocation.
# bash 3.2 (macOS default) has no `mapfile` — use a portable while-read.
RESET_LINES=()
while IFS= read -r _line; do RESET_LINES+=("$_line"); done < <(fmt_resets_all "$SESSION_RESET" "$WEEK_RESET")
SESSION_RESET_TXT="${RESET_LINES[0]:-}"
WEEK_RESET_TXT="${RESET_LINES[1]:-}"

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

  # Reset-ready ping: if a metric dropped sharply since last tick, the window
  # reset - tell the user they are clear to go again. Then remember this tick.
  if [ -f "$LASTSEEN_FILE" ]; then
    read -r LAST_S LAST_W < "$LASTSEEN_FILE" 2>/dev/null
    if [[ "$S_I" =~ ^[0-9]+$ ]] && [[ "$LAST_S" =~ ^[0-9]+$ ]] && [ $((LAST_S - S_I)) -ge "$RESET_DROP" ]; then
      notify "Claude Usage" "Session reset - you are clear to go" "session_reset_$(date +%Y%m%d%H)"
    fi
    if [[ "$W_I" =~ ^[0-9]+$ ]] && [[ "$LAST_W" =~ ^[0-9]+$ ]] && [ $((LAST_W - W_I)) -ge "$RESET_DROP" ]; then
      notify "Claude Usage" "Weekly limit reset - fresh week, clear to go" "weekly_reset_$(date +%Y%m%d)"
    fi
  fi
  if [[ "$S_I" =~ ^[0-9]+$ ]] && [[ "$W_I" =~ ^[0-9]+$ ]]; then
    printf '%s %s\n' "$S_I" "$W_I" > "$LASTSEEN_FILE"
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
  TITLE_COLOR=$(color_for_pct "$S_I")
  # A drawn ring gauge, state-coloured, so the menu bar shows how full the
  # session is without you reading a number. Falls back to the SF Symbol set if
  # rendering is off or unavailable.
  RING=""
  [[ "$S_I" =~ ^[0-9]+$ ]] && RING=$(ring_b64 "$S_I" "$TITLE_COLOR")
  if [ -n "$RING" ]; then
    ICON="image=$RING"
  elif [[ "$S_I" =~ ^[0-9]+$ ]] && [ "$S_I" -ge "$CRIT_PCT" ]; then
    ICON="sfimage=bolt.trianglebadge.exclamationmark"
  elif [[ "$S_I" =~ ^[0-9]+$ ]] && [ "$S_I" -ge "$WARN_PCT" ]; then
    ICON="sfimage=gauge.with.dots.needle.67percent"
  else
    ICON="sfimage=gauge.with.dots.needle.33percent"
  fi
  TITLE="${S_I:-?}%"
  # Weekly stays in the menu bar. It was briefly dropped when the ring arrived,
  # on the theory that the dropdown covered it - but weekly is the number you
  # plan around, and having to open a menu to see it is a real loss. The ring
  # replaces the old SF Symbol, it does not replace data.
  if [[ "$W_I" =~ ^[0-9]+$ ]]; then
    TITLE="$TITLE · ${W_I}%w"
  fi
  # Optional multi-provider glance: append TODAY's local token volume per
  # MENUBAR_MODE (today = the live-changing number, parallel to Claude's %).
  # Read compact figures from the warm summary files (no ordering dependency).
  if [ "$MENUBAR_MODE" = "both" ] && [ -f "$CC_SUMMARY" ]; then
    CC_T=$(jq -r '.today.total_tokens // empty' "$CC_SUMMARY" 2>/dev/null)
    [ -n "$CC_T" ] && TITLE="$TITLE · cc $(humanize_tokens "$CC_T")"
  fi
  if { [ "$MENUBAR_MODE" = "codex" ] || [ "$MENUBAR_MODE" = "both" ]; } && [ -f "$CODEX_SUMMARY" ]; then
    # Prefer the real quota % (from Codex's rate_limits); fall back to today's tokens.
    CX_Q=$(jq -r '.quota.primary.used_percent // empty' "$CODEX_SUMMARY" 2>/dev/null)
    if [ -n "$CX_Q" ]; then
      TITLE="$TITLE · cx $(round "$CX_Q")%"
    else
      CX_T=$(jq -r '.today.tokens // empty' "$CODEX_SUMMARY" 2>/dev/null)
      [ -n "$CX_T" ] && TITLE="$TITLE · cx $(humanize_tokens "$CX_T")"
    fi
  fi
fi

# Provider status: dim + badge the icon if a vendor reports an incident.
# Applies to both credit and normal modes (this runs after the if/else).
STATUS_JSON=$(run_timeout 7 "$PYTHON" "$STATUS_CHECK" 2>/dev/null)
INCIDENT=""
if [ -n "$STATUS_JSON" ]; then
  A_ST=$(echo "$STATUS_JSON" | jq -r '.anthropic // "unknown"')
  O_ST=$(echo "$STATUS_JSON" | jq -r '.openai // "unknown"')
  [ "$A_ST" = "incident" ] && INCIDENT="Claude"
  [ "$O_ST" = "incident" ] && INCIDENT="${INCIDENT:+$INCIDENT + }OpenAI"
fi
if [ -n "$INCIDENT" ]; then
  ICON="sfimage=exclamationmark.triangle.fill"
  TITLE_COLOR="#FF9500"
fi
echo "$TITLE | $ICON $(colorkey "$TITLE_COLOR") size=12"

# --- Dropdown ----------------------------------------------------------------
echo "---"
echo "Claude Usage Dashboard | href=https://claude.ai/settings/usage size=14"
echo "---"

if [ -n "$INCIDENT" ]; then
  echo "⚠ ${INCIDENT} reporting an incident | color=#FF9500 href=https://status.anthropic.com"
  echo "---"
fi

# Heading for the account-limit gauges, so all three sections are labelled the
# same way. Left in the neutral label colour: the gauges under it are already
# colour-coded by state, and a coloured heading here would compete with them.
echo "CLAUDE · account limits | size=12 color=$LBL sfimage=cloud.fill"

# Credits block: only when actually on credits — shown FIRST since it's what
# the user is actively burning down right now.
if [ $ON_CREDITS -eq 1 ]; then
  SPEND_CLR=$(color_for_pct "$SPEND_I")
  SPEND_BAR=$(progress_bar "$SPEND_I")
  echo "On usage credits · ${SPEND_USED_STR} of ${SPEND_LIMIT_STR} (${SPEND_I}%) | size=12 $(colorkey "$SPEND_CLR")"
  echo "$SPEND_BAR | font=Menlo size=12 $(colorkey "$SPEND_CLR")"
  echo "  Weekly limit reached — Claude is billing against your credit pool until ${WEEK_RESET_TXT:-reset}. | size=11 color=$LBL"
  echo "---"
fi

if [ -n "$SESSION" ]; then
  print_metric "Session · 5h" "$SESSION" "$SESSION_RESET_TXT"
  if [ -f "$HISTORY_FILE" ]; then
    SESSION_TREND=$(tail -n 24 "$HISTORY_FILE" 2>/dev/null | awk '{printf "%s ", $2}')
    SPARK=$(sparkline "$SESSION_TREND")
    S_IMG=$(spark_b64 "$SESSION_TREND" "$(color_for_pct "$S_I")")
    if [ -n "$S_IMG" ]; then
      echo "  trend · last 2h | size=11 color=$LBL image=$S_IMG"
    elif [ -n "$SPARK" ]; then
      echo "  trend (last ~2h) $SPARK | font=Menlo size=13 $(colorkey "$(color_for_pct "$S_I")")"
    fi
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
    # Prediction line: only shown when there is a real signal.
    if [ "$PRED_WEEK_VERDICT" = "throttle" ] && [ -n "$PRED_WEEK_ETA" ]; then
      ETA_TXT=$(fmt_resets_all "$PRED_WEEK_ETA")
      echo "  ⚡ at this pace ~100% $ETA_TXT | size=11 $(colorkey "$(color_for_pct 90)")"
    elif [ "$PRED_WEEK_VERDICT" = "headroom" ]; then
      echo "  on track to reset before the cap | size=11 $(colorkey "$(color_for_pct 30)")"
    fi
    echo "---"
  fi
fi

# Per-model weekly limits, dynamic from limits[] (shows Fable/Sonnet/Opus as
# whatever Anthropic currently scopes). One row each.
if [ -n "$SCOPED_LIMITS" ]; then
  while IFS=$'\t' read -r sm_name sm_pct sm_reset; do
    [ -z "$sm_name" ] && continue
    sm_reset_txt=$(fmt_resets_all "$sm_reset")
    print_metric "Weekly · $sm_name" "$sm_pct" "$sm_reset_txt"
    echo "---"
  done <<EOF
$SCOPED_LIMITS
EOF
fi

# Extra usage block — only show when credits are enabled but NOT actively in use,
# so we don't render the credit pool twice (the top "On usage credits" block
# already covers the active case).
if [ "$EXTRA_ENABLED" = "true" ] && [ $ON_CREDITS -eq 0 ]; then
  if [ -n "$SPEND_USED" ] && [ "$SPEND_USED" != "null" ] && [ -n "$SPEND_LIMIT" ]; then
    echo "Credits available · ${SPEND_USED_STR} of ${SPEND_LIMIT_STR} used this month | size=12 color=$LBL"
  else
    echo "Credits enabled · ready when weekly limit hits | size=12 color=$LBL"
  fi
  echo "---"
fi

# --- Claude Code (local token usage) -----------------------------------------
# Read straight from ~/.claude logs — 100% local, no cookies, never expires.
# Call the parser with a short timeout; on timeout fall back to the warm summary
# file so a cold cache never freezes the menu (parser keeps that file updated).
CC_JSON=$(run_timeout 4 "$PYTHON" "$CC_USAGE" 2>/dev/null)
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
  CC_PREV_TOK=$(echo "$CC_JSON"  | jq -r '.prev_week.total_tokens // 0')

  # Model split (top 3 by tokens today)
  CC_MODEL_PARTS=""
  while IFS=$'\t' read -r mname mval; do
    [ -z "$mname" ] && continue
    CC_MODEL_PARTS="$CC_MODEL_PARTS · $mname $(humanize_tokens "$mval")"
  done <<EOF
$(echo "$CC_JSON" | jq -r '.today.by_model | to_entries | map(select(.value > 0)) | sort_by(-.value) | .[0:3][] | "\(.key)\t\(.value)"' 2>/dev/null)
EOF
  CC_MODEL_PARTS="${CC_MODEL_PARTS# · }"

  # Week-over-week delta vs the prior 7 days
  CC_WOW=""
  if [ "$CC_PREV_TOK" -gt 0 ] 2>/dev/null; then
    CC_WOW=$(awk -v w="$CC_WEEK_TOK" -v p="$CC_PREV_TOK" 'BEGIN {
      d = (w - p) / p * 100;
      if (d >= 0) printf "▲%.0f%%", d; else printf "▼%.0f%%", -d;
    }')
  fi

  # 7-day daily token sparkline
  CC_SPARK=$(sparkline "$(echo "$CC_JSON" | jq -r '.daily | join(" ")' 2>/dev/null)")

  echo "CLAUDE CODE | size=12 color=$SEC_CC sfimage=chevron.left.forwardslash.chevron.right"
  echo "Today · $(humanize_tokens "$CC_TODAY_TOK") tokens · ≈$(humanize_usd "$CC_TODAY_USD") value | size=12 color=$LBL"
  [ -n "$CC_MODEL_PARTS" ] && echo "  by model · $CC_MODEL_PARTS | size=11 color=$LBL"
  if [ -n "$CC_WOW" ]; then
    echo "7 days · $(humanize_tokens "$CC_WEEK_TOK") ($CC_WOW vs prev) · 30 days · $(humanize_tokens "$CC_MONTH_TOK") | size=11 color=$LBL"
  else
    echo "7 days · $(humanize_tokens "$CC_WEEK_TOK") · 30 days · $(humanize_tokens "$CC_MONTH_TOK") | size=11 color=$LBL"
  fi
  CC_IMG=$(spark_b64 "$(echo "$CC_JSON" | jq -r '.daily | join(" ")' 2>/dev/null)" "$SEC_CC")
  if [ -n "$CC_IMG" ]; then
    echo "  7-day trend | size=11 color=$LBL image=$CC_IMG"
  elif [ -n "$CC_SPARK" ]; then
    echo "  7-day trend $CC_SPARK | font=Menlo size=11 color=$LBL"
  fi
  # State the window explicitly: this is a rolling 30 days (see claude_code_usage
  # windows["month"], now-29d through today), not a calendar month and not a
  # lifetime total. "/mo" alone read as an all-time figure.
  echo "≈$(humanize_usd "$CC_MONTH_USD") of API value · last 30 days | size=11 color=$LBL"
  echo "---"
fi

# --- Codex (local token usage) -----------------------------------------------
# Read straight from ~/.codex/state_5.sqlite (read-only). Flat-rate ChatGPT
# account, so token volume is the honest metric — no per-token bill.
CODEX_JSON=$(run_timeout 3 "$PYTHON" "$CODEX_USAGE" 2>/dev/null)
if [ -z "$CODEX_JSON" ] && [ -f "$CODEX_SUMMARY" ]; then
  CODEX_JSON=$(cat "$CODEX_SUMMARY")
fi

if [ -n "$CODEX_JSON" ] && echo "$CODEX_JSON" | jq -e '.available == true' >/dev/null 2>&1; then
  CX_TODAY_TOK=$(echo "$CODEX_JSON" | jq -r '.today.tokens // 0')
  CX_TODAY_THR=$(echo "$CODEX_JSON" | jq -r '.today.threads // 0')
  CX_WEEK_TOK=$(echo "$CODEX_JSON"  | jq -r '.week.tokens // 0')
  CX_MONTH_TOK=$(echo "$CODEX_JSON" | jq -r '.month.tokens // 0')
  CX_ALL_TOK=$(echo "$CODEX_JSON"   | jq -r '.all_time.tokens // 0')

  CX_PREV_TOK=$(echo "$CODEX_JSON" | jq -r '.prev_week.tokens // 0')
  CX_WOW=""
  if [ "$CX_PREV_TOK" -gt 0 ] 2>/dev/null; then
    CX_WOW=$(awk -v w="$CX_WEEK_TOK" -v p="$CX_PREV_TOK" 'BEGIN {
      d = (w - p) / p * 100;
      if (d >= 0) printf "▲%.0f%%", d; else printf "▼%.0f%%", -d;
    }')
  fi
  CX_SPARK=$(sparkline "$(echo "$CODEX_JSON" | jq -r '.daily | join(" ")' 2>/dev/null)")

  CX_THR_LABEL="threads"; [ "$CX_TODAY_THR" = "1" ] && CX_THR_LABEL="thread"
  echo "CODEX | size=12 color=$SEC_CX sfimage=curlybraces"
  # Real quota gauge from Codex's own rate_limits (primary + optional secondary window).
  CX_Q_PCT=$(echo "$CODEX_JSON" | jq -r '.quota.primary.used_percent // empty')
  if [ -n "$CX_Q_PCT" ]; then
    CX_Q_WIN=$(echo "$CODEX_JSON" | jq -r '.quota.primary.window // "weekly"')
    CX_Q_RESET=$(echo "$CODEX_JSON" | jq -r '.quota.primary.resets_at // empty')
    CX_Q_RESET_TXT=""
    [ -n "$CX_Q_RESET" ] && CX_Q_RESET_TXT=$(fmt_resets_all "$(date -r "${CX_Q_RESET%.*}" +%Y-%m-%dT%H:%M:%S%z 2>/dev/null)")
    print_metric "Quota · $CX_Q_WIN" "$CX_Q_PCT" "$CX_Q_RESET_TXT"
    # Secondary window (e.g. a shorter burst limit) when Codex reports one.
    CX_Q2_PCT=$(echo "$CODEX_JSON" | jq -r '.quota.secondary.used_percent // empty')
    if [ -n "$CX_Q2_PCT" ]; then
      CX_Q2_WIN=$(echo "$CODEX_JSON" | jq -r '.quota.secondary.window // "5h"')
      print_metric "Quota · $CX_Q2_WIN" "$CX_Q2_PCT" ""
    fi
  fi
  echo "Today · $(humanize_tokens "$CX_TODAY_TOK") tokens · ${CX_TODAY_THR} ${CX_THR_LABEL} | size=12 color=$LBL"
  if [ -n "$CX_WOW" ]; then
    echo "7 days · $(humanize_tokens "$CX_WEEK_TOK") ($CX_WOW vs prev) · 30 days · $(humanize_tokens "$CX_MONTH_TOK") · all-time · $(humanize_tokens "$CX_ALL_TOK") | size=11 color=$LBL"
  else
    echo "7 days · $(humanize_tokens "$CX_WEEK_TOK") · 30 days · $(humanize_tokens "$CX_MONTH_TOK") · all-time · $(humanize_tokens "$CX_ALL_TOK") | size=11 color=$LBL"
  fi
  CX_IMG=$(spark_b64 "$(echo "$CODEX_JSON" | jq -r '.daily | join(" ")' 2>/dev/null)" "$SEC_CX")
  if [ -n "$CX_IMG" ]; then
    echo "  7-day trend | size=11 color=$LBL image=$CX_IMG"
  elif [ -n "$CX_SPARK" ]; then
    echo "  7-day trend $CX_SPARK | font=Menlo size=11 color=$LBL"
  fi
  echo "---"
fi

# Footer
echo "---"
echo "↻ Refresh now | refresh=true sfimage=arrow.clockwise"
echo "Copy status | bash='/bin/bash' param1='-c' param2='\"$PYTHON\" \"$COPY_SUMMARY\" | pbcopy' terminal=false sfimage=doc.on.clipboard"
echo "Export usage | sfimage=square.and.arrow.up"
echo "-- as CSV | bash='$PYTHON' param1='$EXPORT' param2='csv' terminal=false"
echo "-- as JSON | bash='$PYTHON' param1='$EXPORT' param2='json' terminal=false"

# Alerts submenu: mute controls + current mute status.
if [ -f "$MUTE_FILE" ] && [ "$(cat "$MUTE_FILE" 2>/dev/null)" -gt "$(date +%s)" ] 2>/dev/null; then
  MUTE_TXT=$(fmt_resets_all "$(date -r "$(cat "$MUTE_FILE")" +%Y-%m-%dT%H:%M:%S%z 2>/dev/null)")
  echo "Alerts muted ${MUTE_TXT:+(unmutes $MUTE_TXT)} | sfimage=bell.slash"
else
  echo "Alerts | sfimage=bell"
fi
echo "-- Mute 1 hour | bash='/bin/bash' param1='-c' param2='echo \$(( \$(date +%s)+3600 )) > \"$MUTE_FILE\"' terminal=false refresh=true"
echo "-- Mute until tomorrow 9am | bash='/bin/bash' param1='-c' param2='echo \$(date -j -f %Y-%m-%d-%H:%M:%S \"\$(date -v+1d +%Y-%m-%d)-09:00:00\" +%s) > \"$MUTE_FILE\"' terminal=false refresh=true"
echo "-- Unmute | bash='/bin/rm' param1='-f' param2='$MUTE_FILE' terminal=false refresh=true"

# --- Updates -----------------------------------------------------------------
# Read the cached update status (instant); refresh it in the background if stale
# so we never block the render on a network git fetch. Computed before the menu
# below so the submenu can show the current build.
UPD_BEHIND=0; UPD_SHA="?"; UPD_TS=0
if [ -f "$UPDATE_STATUS" ]; then
  read -r UPD_BEHIND UPD_SHA UPD_TS < "$UPDATE_STATUS"
fi
if [ ! -f "$UPDATE_STATUS" ] || { [ -n "$UPD_TS" ] && [ $(( $(date +%s) - UPD_TS )) -gt "$UPDATE_CHECK_INTERVAL" ]; } 2>/dev/null; then
  ( bash "$CHECK_UPDATE" >/dev/null 2>&1 & )
fi

# Settings, config, logs and update controls are things you touch a couple of
# times a year, so they no longer sit in the same space as the numbers you read
# every day. One "More" submenu holds the lot.
echo "More | sfimage=ellipsis.circle"
echo "-- Open settings | href=https://claude.ai/settings/usage"
echo "-- Edit config | bash='/usr/bin/open' param1='-t' param2='$CONFIG' terminal=false"
echo "-- Force cookie refresh | bash='$PYTHON' param1='$REFRESHER' terminal=false refresh=true"
echo "-----"
echo "-- View raw JSON | bash='/usr/bin/open' param1='-t' param2='$RAW' terminal=false"
echo "-- View error log | bash='/usr/bin/open' param1='-t' param2='$ERR_LOG' terminal=false"
echo "-----"
echo "-- Check for updates | bash='/bin/bash' param1='$CHECK_UPDATE' terminal=false refresh=true"
echo "-- View on GitHub | href=$REPO_URL"
echo "-- Build $UPD_SHA | color=$LBL"

# "Up to date" is not news, so it stays in the submenu. Only surface an update
# at top level, where it is something you can actually act on.
if [ "${UPD_BEHIND:-0}" -gt 0 ] 2>/dev/null; then
  echo "---"
  echo "⬆ Update available ($UPD_BEHIND new) | color=#FF9500 sfimage=arrow.down.circle.fill"
  echo "Update now | bash='/bin/bash' param1='$UPDATE_SCRIPT' terminal=false refresh=true sfimage=arrow.down.circle"
fi
