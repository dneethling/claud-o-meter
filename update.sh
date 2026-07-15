#!/bin/bash
# Pull the latest widget from GitHub and refresh Python deps. Safe: uses
# --ff-only so it never creates a merge or clobbers local edits (it just
# reports if the tree has diverged). The user's config lives outside the repo
# (~/.claude-usage-widget.conf) so it is never touched.

WIDGET_DIR="${CLAUDE_WIDGET_DIR:-$(cd "$(dirname "${BASH_SOURCE[0]:-$0}")" && pwd)}"
cd "$WIDGET_DIR" 2>/dev/null || exit 1

OUT=$(git pull --ff-only 2>&1)
RC=$?

if [ $RC -eq 0 ]; then
  # Refresh deps if requirements changed (cheap no-op when already satisfied).
  [ -x ./.venv/bin/pip ] && [ -f requirements.txt ] && ./.venv/bin/pip install -q -r requirements.txt 2>/dev/null
  # Refresh the cached update status.
  bash "$WIDGET_DIR/check_update.sh" 2>/dev/null
  osascript -e 'display notification "Widget updated to the latest version" with title "Claude Usage" sound name "Glass"' 2>/dev/null
else
  # Diverged (local edits) - do not clobber; tell the user.
  osascript -e "display notification \"Update needs attention: local changes. Run 'git status' in the widget folder.\" with title \"Claude Usage\"" 2>/dev/null
  echo "$OUT" >&2
fi
