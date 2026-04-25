#!/bin/bash
# Builds a DMG from the .app bundle with a drag-to-Applications layout.
set -euo pipefail

APP_NAME="Claud-o-meter"
APP_PATH="build/${APP_NAME}.app"
DMG_NAME="${APP_NAME}-v1.0.0.dmg"
DMG_DIR="build/dmg"

if [ ! -d "$APP_PATH" ]; then
    echo "Error: $APP_PATH not found. Run 'make app' first."
    exit 1
fi

echo "Creating DMG..."
rm -rf "$DMG_DIR" "build/$DMG_NAME"
mkdir -p "$DMG_DIR"

cp -R "$APP_PATH" "$DMG_DIR/"
ln -s /Applications "$DMG_DIR/Applications"

hdiutil create -volname "$APP_NAME" \
    -srcfolder "$DMG_DIR" \
    -ov -format UDZO \
    "build/$DMG_NAME"

rm -rf "$DMG_DIR"
echo "✅ Created build/$DMG_NAME"
