#!/bin/bash
DIR="$(cd "$(dirname "$0")/.." && pwd)"
# shellcheck source=/dev/null
source "$DIR/tests/assert.sh"
# shellcheck source=/dev/null
source "$DIR/lib/format.sh"

assert_eq "$(color_for_pct 30)"  "#34C759" "color green under 60"
assert_eq "$(color_for_pct 70)"  "#FF9500" "color orange 60-84"
assert_eq "$(color_for_pct 90)"  "#FF3B30" "color red 85+"
assert_eq "$(color_for_pct '')"  "#34C759" "color defaults green on empty"

# Themes
assert_eq "$(THEME=colorblind color_for_pct 30)" "#0072B2" "colorblind low = blue"
assert_eq "$(THEME=colorblind color_for_pct 70)" "#E69F00" "colorblind warn = orange"
assert_eq "$(THEME=colorblind color_for_pct 90)" "#D55E00" "colorblind crit = vermillion"
assert_eq "$(THEME=minimal color_for_pct 90)"    ""        "minimal returns empty"
assert_eq "$(THEME=minimal color_for_pct 30)"    ""        "minimal returns empty (low)"

# colorkey guard (prevents the black-on-black bug)
assert_eq "$(colorkey '#FF3B30')" "color=#FF3B30" "colorkey wraps non-empty"
assert_eq "$(colorkey '')"        ""              "colorkey empty -> nothing"

assert_eq "$(humanize_tokens 532362776)"  "532M" "humanize 532M"
assert_eq "$(humanize_tokens 4030338896)" "4.0B" "humanize 4.0B"
assert_eq "$(humanize_tokens 66105)"      "66k"  "humanize 66k"
assert_eq "$(humanize_tokens 512)"        "512"  "humanize raw"

assert_eq "$(humanize_usd 1551)" "\$1.6k" "usd 1.6k"
assert_eq "$(humanize_usd 52)"   "\$52"   "usd 52"

assert_eq "$(round 16.4)" "16" "round down"
assert_eq "$(round 16.6)" "17" "round up"

# Sparkline: 8 levels ▁▂▃▄▅▆▇█ mapped linearly from min..max across the inputs.
assert_eq "$(sparkline "0 0 0")"        "▁▁▁"     "sparkline flat = all low"
assert_eq "$(sparkline "0 100")"        "▁█"      "sparkline min and max"
assert_eq "$(sparkline "0 50 100")"     "▁▅█"     "sparkline midpoint"
assert_eq "$(sparkline "")"             ""        "sparkline empty input"
assert_eq "$(sparkline "42")"           "▁"       "sparkline single value = low"

finish
