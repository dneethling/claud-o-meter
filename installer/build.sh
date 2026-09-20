#!/bin/bash
# Produce a double-clickable macOS app from the reviewed installer source.
set -euo pipefail
ROOT=$(cd "$(dirname "$0")/.." && pwd)
OUT="${1:-$ROOT/build/installer}"
mkdir -p "$OUT"
# Never overwrite an existing bundle silently.
APP="$OUT/Claud-o-meter Setup.app"
[ ! -e "$APP" ] || { echo "Output already exists: $APP" >&2; exit 1; }
osacompile -o "$APP" "$ROOT/installer/Installer.applescript"
cp "$ROOT/install.sh" "$APP/Contents/Resources/install.sh"
/usr/libexec/PlistBuddy -c 'Set :CFBundleIdentifier com.darren.claudometer.setup' "$APP/Contents/Info.plist" 2>/dev/null \
  || /usr/libexec/PlistBuddy -c 'Add :CFBundleIdentifier string com.darren.claudometer.setup' "$APP/Contents/Info.plist"
# Developer ID and notarization can be supplied by the distributor separately.
codesign --force --sign - "$APP"
codesign --verify --strict "$APP"
ditto -c -k --sequesterRsrc --keepParent "$APP" "$OUT/Claud-o-meter-Setup.zip"
echo "$OUT/Claud-o-meter-Setup.zip"
