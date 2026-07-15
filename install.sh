#!/bin/bash
# One-command installer for the Claude/Codex usage widget.
#   curl -fsSL https://raw.githubusercontent.com/dneethling/claud-o-meter/master/install.sh | bash
# or, after cloning:  ./install.sh [install-dir]
set -e

REPO_URL="https://github.com/dneethling/claud-o-meter.git"
DIR="${1:-$HOME/claud-o-meter}"

echo "== Claud-o-meter installer =="

# 0. Prerequisites: Homebrew (for jq/SwiftBar), git, python3
if ! command -v brew >/dev/null 2>&1; then
  echo "Homebrew is required. Install it from https://brew.sh then re-run." >&2
  exit 1
fi

# 1. Clone or update the repo.
if [ -d "$DIR/.git" ]; then
  echo "Updating existing install at $DIR ..."
  git -C "$DIR" pull --ff-only
else
  echo "Cloning to $DIR ..."
  git clone "$REPO_URL" "$DIR"
fi
cd "$DIR"

# 2. Python venv + dependencies.
echo "Setting up the Python environment ..."
python3 -m venv .venv
./.venv/bin/pip install -q --upgrade pip
./.venv/bin/pip install -q -r requirements.txt

# 3. Command-line tools.
command -v jq >/dev/null 2>&1 || { echo "Installing jq ..."; brew install jq; }
if ! ls -d /Applications/SwiftBar.app >/dev/null 2>&1; then
  echo "Installing SwiftBar ..."
  brew install --cask swiftbar
fi

# 4. Config: detect the Claude org from the browser, then pull a validated cookie.
echo "Detecting your Claude session from your browser ..."
if ! ./.venv/bin/python setup_config.py; then
  echo "Log into https://claude.ai in Arc, Chrome, or Brave, then re-run this installer." >&2
  exit 1
fi
./.venv/bin/python refresh_cookie.py || echo "(the cookie will refresh automatically on the schedule)"

# 5. Point SwiftBar at the repo's plugins folder.
defaults write com.ameba.SwiftBar PluginDirectory -string "$DIR/plugins"
defaults write com.ameba.SwiftBar DisablePluginsUpdates -bool true 2>/dev/null || true

# 6. Install the periodic cookie-refresh LaunchAgent (paths baked in for this machine).
PLIST="$HOME/Library/LaunchAgents/com.claudometer.refresh.plist"
cat > "$PLIST" <<PL
<?xml version="1.0" encoding="UTF-8"?>
<!DOCTYPE plist PUBLIC "-//Apple//DTD PLIST 1.0//EN" "http://www.apple.com/DTDs/PropertyList-1.0.dtd">
<plist version="1.0"><dict>
  <key>Label</key><string>com.claudometer.refresh</string>
  <key>ProgramArguments</key><array>
    <string>$DIR/.venv/bin/python</string>
    <string>$DIR/refresh_cookie.py</string>
  </array>
  <key>StartInterval</key><integer>1800</integer>
  <key>RunAtLoad</key><true/>
  <key>StandardErrorPath</key><string>/tmp/claude-usage-refresh.err</string>
  <key>EnvironmentVariables</key><dict>
    <key>HOME</key><string>$HOME</string>
  </dict>
</dict></plist>
PL
launchctl unload "$PLIST" 2>/dev/null || true
launchctl load "$PLIST" 2>/dev/null || true

# 7. Prime the update-status cache and launch.
bash "$DIR/check_update.sh" >/dev/null 2>&1 || true
open -a SwiftBar 2>/dev/null || true

echo ""
echo "Done. Look for the gauge icon in your menu bar (top right)."
echo "If it shows 'Re-auth', just make sure you are logged into claude.ai in your browser - it recovers on its own."
echo "To update later: click the icon -> Update now (or it prompts you when a new version is pushed)."
