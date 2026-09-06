#!/bin/bash
set -euo pipefail
cd "$(dirname "$0")/.."
repo=dneethling/claud-o-meter
dist=build/distribution
test -f "$dist/appcast.xml"
tag=$(cat "$dist/tag.txt")
commit=$(cat "$dist/commit.txt")
case "$tag" in notch-v*) ;; *) echo 'Invalid release tag.'; exit 1;; esac
version=${tag#notch-v}
test -f "$dist/ClaudOMeterNotch-$version.zip"
gh release create "$tag" "$dist/ClaudOMeterNotch-$version.zip" \
  --repo "$repo" --target "$commit" --title "Claud-o-meter Notch $version" \
  --notes "Standalone notch edition. Requires macOS 26 or later. Install the app in Applications; updates are delivered through its own signed channel." --latest=false
if ! gh release view notch-updates --repo "$repo" >/dev/null 2>&1; then
  gh release create notch-updates --repo "$repo" --target "$commit" \
    --title 'Notch update channel' --notes 'Stable Sparkle feed for Claud-o-meter Notch.' --prerelease --latest=false
fi
gh release upload notch-updates "$dist/appcast.xml" --clobber --repo "$repo"
echo "Published $tag and updated the notch feed."
