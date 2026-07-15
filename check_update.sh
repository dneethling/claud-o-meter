#!/bin/bash
# Check whether the widget repo is behind its GitHub origin, without changing
# anything. Writes "<behind_count> <local_short_sha> <epoch>" to a status file
# that the plugin reads. Safe to run in the background on a schedule.

WIDGET_DIR="${CLAUDE_WIDGET_DIR:-$(cd "$(dirname "${BASH_SOURCE[0]:-$0}")" && pwd)}"
STATUS="$HOME/.claude-usage-update-status"

cd "$WIDGET_DIR" 2>/dev/null || exit 0
git rev-parse --is-inside-work-tree >/dev/null 2>&1 || { printf '0 nogit %s\n' "$(date +%s)" > "$STATUS"; exit 0; }

# Fetch quietly (network); ignore failures (offline -> keep last status).
git fetch --quiet origin 2>/dev/null

BRANCH=$(git rev-parse --abbrev-ref HEAD 2>/dev/null)
LOCAL=$(git rev-parse --short HEAD 2>/dev/null)
BEHIND=$(git rev-list --count "HEAD..origin/${BRANCH}" 2>/dev/null)
[ -z "$BEHIND" ] && BEHIND=0

printf '%s %s %s\n' "$BEHIND" "${LOCAL:-?}" "$(date +%s)" > "$STATUS"

# Optional auto-update: if the user set AUTO_UPDATE=1 in the config, pull now.
if grep -q '^AUTO_UPDATE=1' "$HOME/.claude-usage-widget.conf" 2>/dev/null && [ "$BEHIND" -gt 0 ] 2>/dev/null; then
  bash "$WIDGET_DIR/update.sh" >/dev/null 2>&1
fi
