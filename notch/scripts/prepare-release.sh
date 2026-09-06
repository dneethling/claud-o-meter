#!/bin/bash
set -euo pipefail
cd "$(dirname "$0")/.."
: "${DEVELOPER_TEAM_ID:?Set your Apple team ID}"
: "${DEVELOPER_SIGNING_IDENTITY:?Set your Developer ID Application identity}"
: "${SPARKLE_PUBLIC_KEY:?Set your Sparkle public key}"
test "$(uname -s)" = Darwin || { echo 'Release preparation requires macOS.'; exit 1; }
test -z "$(git status --porcelain)" || { echo 'Commit or stash changes before preparing a release.'; exit 1; }
make gen
dist="$PWD/build/distribution"
test ! -e "$dist" || { echo 'Move the previous build/distribution aside first.'; exit 1; }
mkdir -p "$dist"
xcodebuild -project ClaudOMeterNotch.xcodeproj -scheme ClaudOMeterNotch \
  -configuration Release -destination 'generic/platform=macOS' -derivedDataPath build \
  -archivePath build/ClaudOMeterNotch.xcarchive \
  DEVELOPMENT_TEAM="$DEVELOPER_TEAM_ID" CODE_SIGN_IDENTITY="$DEVELOPER_SIGNING_IDENTITY" \
  SPARKLE_PUBLIC_KEY="$SPARKLE_PUBLIC_KEY" ARCHS='arm64 x86_64' ONLY_ACTIVE_ARCH=NO archive
python3 - <<'PY'
import os, plistlib
with open('build/ExportOptions.plist', 'wb') as f:
    plistlib.dump({'method': 'developer-id', 'teamID': os.environ['DEVELOPER_TEAM_ID'],
                  'signingStyle': 'manual', 'signingCertificate': os.environ['DEVELOPER_SIGNING_IDENTITY']}, f)
PY
xcodebuild -exportArchive -archivePath build/ClaudOMeterNotch.xcarchive \
  -exportOptionsPlist build/ExportOptions.plist -exportPath build/export
app="$PWD/build/export/ClaudOMeterNotch.app"
codesign --verify --deep --strict "$app"
ditto -c -k --sequesterRsrc --keepParent "$app" build/notary-upload.zip
xcrun notarytool submit build/notary-upload.zip --keychain-profile ClaudOMeterNotch --wait
xcrun stapler staple "$app"
xcrun stapler validate "$app"
spctl --assess --type execute "$app"
version=$(/usr/libexec/PlistBuddy -c 'Print :CFBundleShortVersionString' "$app/Contents/Info.plist")
tag="notch-v$version"
archive="ClaudOMeterNotch-$version.zip"
ditto -c -k --sequesterRsrc --keepParent "$app" "$dist/$archive"
appcast_tool=$(find build/SourcePackages/artifacts/sparkle -type f -name generate_appcast -print -quit)
test -n "$appcast_tool" || { echo 'Sparkle generate_appcast not found.'; exit 1; }
"$appcast_tool" "$dist" --download-url-prefix "https://github.com/dneethling/claud-o-meter/releases/download/$tag/"
python3 - "$dist/appcast.xml" <<'PY'
import sys, xml.etree.ElementTree as ET
root = ET.parse(sys.argv[1]).getroot()
enclosures = root.findall('.//enclosure')
assert enclosures and all(e.get('{http://www.andymatuschak.org/xml-namespaces/sparkle}edSignature') for e in enclosures), 'Missing update signatures'
PY
printf '%s\n' "$tag" > "$dist/tag.txt"
git rev-parse HEAD > "$dist/commit.txt"
echo "Prepared $tag in $dist. Inspect and test it before running publish-release.sh."
