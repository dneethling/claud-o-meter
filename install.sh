#!/bin/bash
# One-command installer for the Claude/Codex usage widget.
#   curl -fsSL https://raw.githubusercontent.com/dneethling/claud-o-meter/master/install.sh | bash
# or, after cloning:  ./install.sh [install-dir]
set -euo pipefail
export PATH="/opt/homebrew/bin:/usr/local/bin:$PATH"

REPO_URL="https://github.com/dneethling/claud-o-meter.git"
DIR="${1:-$HOME/claud-o-meter}"

echo "== Claud-o-meter installer =="
if [ "$(uname -s)" != Darwin ]; then
  echo "Claud-o-meter requires macOS." >&2
  exit 1
fi
if ! xcode-select -p >/dev/null 2>&1; then
  echo "Apple Command Line Tools are required. Install them, then run setup again." >&2
  xcode-select --install 2>/dev/null || true
  exit 1
fi

# 0. Prerequisites: Homebrew (for jq/SwiftBar), git, python3
if ! command -v brew >/dev/null 2>&1; then
  echo "Homebrew is required. Install it from https://brew.sh then re-run." >&2
  exit 1
fi

# 1. Clone or update the repo.
if [ -d "$DIR/.git" ]; then
  echo "Updating existing install at $DIR ..."
  if [ "$(git -C "$DIR" remote get-url origin)" != "$REPO_URL" ]; then
    echo "That folder belongs to another repository. Choose a different install folder." >&2
    exit 1
  fi
  if [ -n "$(git -C "$DIR" status --porcelain)" ]; then
    echo "Local changes found. They have been kept; review them before installing." >&2
    exit 1
  fi
  git -C "$DIR" pull --ff-only
else
  echo "Cloning to $DIR ..."
  git clone "$REPO_URL" "$DIR"
fi
cd "$DIR"
DIR="$(pwd -P)"

# 2. Python venv + dependencies.
echo "Setting up the Python environment ..."
if ! python3 -c 'import sys; sys.exit(sys.version_info < (3, 10))' 2>/dev/null; then
  echo "Installing Python 3.10 or newer ..."
  brew install python
fi
python3 -m venv .venv
./.venv/bin/pip install -q --upgrade pip
./.venv/bin/pip install -q -r requirements.txt

# 3. Command-line tools.
command -v jq >/dev/null 2>&1 || { echo "Installing jq ..."; brew install jq; }
if [ ! -d /Applications/SwiftBar.app ] && [ ! -d "$HOME/Applications/SwiftBar.app" ]; then
  echo "Installing SwiftBar ..."
  brew install --cask swiftbar
fi

# 4. Config: detect the Claude org from the app/browser, then pull a validated cookie.
echo "Detecting your Claude session (Claude app or browser) ..."
if ! ./.venv/bin/python setup_config.py; then
  echo "Sign in to the Claude desktop app (or https://claude.ai in Arc/Chrome/Brave), then re-run this installer." >&2
  exit 1
fi
./.venv/bin/python refresh_cookie.py || echo "(the cookie will refresh automatically on the schedule)"

# 5. Point SwiftBar at the repo's plugins folder.
PLUGIN_DIR=$(defaults read com.ameba.SwiftBar PluginDirectory 2>/dev/null || true)
if [ -n "$PLUGIN_DIR" ] && [ "$PLUGIN_DIR" != "$DIR/plugins" ]; then
  # Keep existing widgets. A launcher points to the managed repository so
  # future updates still apply without copying the implementation.
  "$DIR/.venv/bin/python" "$DIR/install_plugin.py" "$PLUGIN_DIR" "$DIR"
else
  defaults write com.ameba.SwiftBar PluginDirectory -string "$DIR/plugins"
fi
defaults write com.ameba.SwiftBar DisablePluginsUpdates -bool true 2>/dev/null || true

# 6. Reconcile the launchd background agents (the 30-min cookie refresher, and
# retire any legacy/duplicate agents). Idempotent, and shared with update.sh so
# updates fix background jobs too - not just code. The weekly rate check is now
# driven from the plugin, so there is no separate pricing agent here.
bash "$DIR/agents.sh" "$DIR"

# 7. Prime the update-status cache and launch.
bash "$DIR/check_update.sh" >/dev/null 2>&1 || true
open -a SwiftBar

echo ""
echo "Done. Look for the gauge icon in your menu bar (top right)."
echo "If it shows 'Re-auth', just make sure you are signed into the Claude app or claude.ai in your browser - it recovers on its own."
echo "To update later: click the icon -> Update now (or it prompts you when a new version is pushed)."
