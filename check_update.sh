#!/bin/bash
# Preserve the existing entry point used by background checks and the menu.
WIDGET_DIR="${CLAUDE_WIDGET_DIR:-$(cd "$(dirname "${BASH_SOURCE[0]:-$0}")" && pwd)}"
exec "$WIDGET_DIR/.venv/bin/python" "$WIDGET_DIR/widget_update.py" check
