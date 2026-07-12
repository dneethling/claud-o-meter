#!/bin/bash
DIR="$(cd "$(dirname "$0")/.." && pwd)"
# shellcheck source=/dev/null
source "$DIR/tests/assert.sh"

# append_sample <file> <value> <cap> keeps at most <cap> newest lines.
append_sample() {
  local file="$1" value="$2" cap="$3"
  echo "$value" >> "$file"
  local n; n=$(wc -l < "$file" | tr -d ' ')
  if [ "$n" -gt "$cap" ]; then
    tail -n "$cap" "$file" > "$file.tmp" && mv -f "$file.tmp" "$file"
  fi
}

TMP="/tmp/test-history-$$.txt"
rm -f "$TMP"
for v in 1 2 3 4 5; do append_sample "$TMP" "$v" 3; done
assert_eq "$(wc -l < "$TMP" | tr -d ' ')" "3" "history capped at 3 lines"
assert_eq "$(tr '\n' ' ' < "$TMP" | sed 's/ $//')" "3 4 5" "history keeps newest"
rm -f "$TMP"
finish
