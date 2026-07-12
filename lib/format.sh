#!/bin/bash
# Pure display/formatting helpers, sourced by the SwiftBar plugin and unit tests.
# No side effects, no globals beyond the threshold constants below.

WARN_PCT="${WARN_PCT:-60}"
CRIT_PCT="${CRIT_PCT:-85}"

color_for_pct() {
  local pct="${1%%.*}"
  [ -z "$pct" ] && { echo "#34C759"; return; }
  if [ "$pct" -ge "$CRIT_PCT" ]; then
    echo "#FF3B30"
  elif [ "$pct" -ge "$WARN_PCT" ]; then
    echo "#FF9500"
  else
    echo "#34C759"
  fi
}

progress_bar() {
  local pct="${1%%.*}"
  [ -z "$pct" ] && { echo ""; return; }
  local width=14
  local filled=$(( pct * width / 100 ))
  [ $filled -gt $width ] && filled=$width
  [ $filled -lt 0 ] && filled=0
  local empty=$(( width - filled ))
  local bar="" i
  for ((i=0; i<filled; i++)); do bar+="█"; done
  for ((i=0; i<empty; i++)); do bar+="░"; done
  echo "$bar"
}

round() { [ -n "$1" ] && printf "%.0f" "$1" || echo ""; }

humanize_tokens() {
  local n="$1"
  { [ -z "$n" ] || [ "$n" = "null" ]; } && { echo "—"; return; }
  awk -v n="$n" 'BEGIN {
    if (n >= 1e9)      printf "%.1fB", n/1e9;
    else if (n >= 1e6) printf "%.0fM", n/1e6;
    else if (n >= 1e3) printf "%.0fk", n/1e3;
    else               printf "%d", n;
  }'
}

humanize_usd() {
  local n="$1"
  { [ -z "$n" ] || [ "$n" = "null" ]; } && { echo "—"; return; }
  awk -v n="$n" 'BEGIN {
    if (n >= 1000) printf "$%.1fk", n/1000;
    else           printf "$%.0f", n;
  }'
}

format_money() {
  local amt="$1" cur="$2" exp="$3"
  if [ -z "$amt" ] || [ "$amt" = "null" ]; then echo "—"; return; fi
  local divisor=1 i=0
  while [ "$i" -lt "${exp:-0}" ]; do divisor=$(( divisor * 10 )); i=$(( i + 1 )); done
  local val fmt="%.${exp:-0}f"
  val=$(awk -v a="$amt" -v d="$divisor" -v f="$fmt" 'BEGIN { printf f, a/d }')
  case "$cur" in
    USD) echo "\$$val" ;;
    GBP) echo "£$val" ;;
    EUR) echo "€$val" ;;
    ZAR) echo "R$val" ;;
    JPY) echo "¥$val" ;;
    *)   echo "$val $cur" ;;
  esac
}

# Render a Unicode sparkline from space-separated numbers.
# 8 levels ▁▂▃▄▅▆▇█ mapped linearly across [min,max]. All-equal input -> all ▁.
# awk computes integer levels only (0-7); bash maps them to glyphs, because
# macOS awk splits multibyte UTF-8 by byte, not character.
sparkline() {
  local nums="$1"
  [ -z "$nums" ] && { echo ""; return; }
  local glyphs=(▁ ▂ ▃ ▄ ▅ ▆ ▇ █)
  local levels
  levels=$(awk -v s="$nums" 'BEGIN {
    n = split(s, a, " ");
    if (n == 0) { exit }
    mn = a[1]; mx = a[1];
    for (i = 1; i <= n; i++) { if (a[i] < mn) mn = a[i]; if (a[i] > mx) mx = a[i]; }
    for (i = 1; i <= n; i++) {
      if (mx == mn) { lvl = 0 }
      else { lvl = int(((a[i] - mn) / (mx - mn)) * 7 + 0.5) }
      printf "%d ", lvl;
    }
  }')
  local out="" lvl
  for lvl in $levels; do
    out="$out${glyphs[$lvl]}"
  done
  echo "$out"
}
