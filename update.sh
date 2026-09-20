#!/bin/bash
# The helper records failures honestly and only fast-forwards a clean checkout.
WIDGET_DIR="${CLAUDE_WIDGET_DIR:-$(cd "$(dirname "${BASH_SOURCE[0]:-$0}")" && pwd)}"
if "$WIDGET_DIR/.venv/bin/python" "$WIDGET_DIR/widget_update.py" install; then
  osascript -e 'display notification "Update installed. Refresh the widget to use it." with title "Claud-o-meter"' 2>/dev/null || true
else
  osascript -e 'display notification "Update incomplete. Open the widget menu for details and retry." with title "Claud-o-meter"' 2>/dev/null || true
  exit 1
fi
