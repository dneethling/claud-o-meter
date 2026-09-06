# Publish and update installed copies

This app uses Sparkle. Released copies check Darren's feed daily, verify the
update archive signature, and apply updates at relaunch. Users can disable
automatic updates or check manually. This is polling, not an instant remote push.
Only this notch app participates; SwiftBar continues its existing update method.

Release contact: darren@dcai.co.za

## One-time setup on your release Mac

1. Install full Xcode 26+, XcodeGen and GitHub CLI. Sign in to GitHub CLI with
   access to `dneethling/claud-o-meter`.
2. Install your Apple **Developer ID Application** certificate and private key
   in Keychain. Keep the same identity for releases so Keychain access grants
   survive updates. This requires Apple Developer Program membership.
3. Run `make build`. Find Sparkle's `bin/generate_keys` inside
   `build/SourcePackages/artifacts/sparkle/` and run it once. Keep its private key
   in your Mac's Keychain; back it up securely. The printed **public** key is safe
   to commit. Never commit an exported private key.
4. Set `SPARKLE_PUBLIC_KEY` in `project.yml` to that public key, so all distributed
   builds trust the same key. The release script also requires it as an environment
   variable, and overrides the build setting with that value.
5. Store notarization credentials using `xcrun notarytool store-credentials
   ClaudOMeterNotch`. If `darren@dcai.co.za` is your Apple Developer Apple ID, use it at the prompt.
   The email alone does not establish membership or install a certificate.

The feed is deliberately **not** upstream's feed or GitHub's generic latest-release
URL. It is the fixed `notch-updates` release asset, so unrelated SwiftBar releases
cannot redirect the app's updates.

## Prepare a release

Update `MARKETING_VERSION`, increment `CURRENT_PROJECT_VERSION` in `project.yml`,
and add release notes in `Sources/Settings/ReleaseNotes.swift`. Run `make test`.
Use a clean checkout containing the intended release and committed public key.

```sh
export DEVELOPER_TEAM_ID='your Apple team ID'
export DEVELOPER_SIGNING_IDENTITY='Developer ID Application: Your Name (TEAMID)'
export SPARKLE_PUBLIC_KEY='your public Ed25519 key'
./scripts/prepare-release.sh
```

The script archives, exports, notarizes and staples the app, packages a versioned
ZIP, then generates its signed update enclosure. No release is published by this
step. The generated assets are under `build/distribution/`.

## Publish the prepared release

```sh
./scripts/publish-release.sh
```

This publishes a new versioned release and uploads the prepared `appcast.xml` to
the fixed `notch-updates` channel. The release tag is tied to the commit recorded
at preparation time. A version tag cannot be reused. If uploading the channel
fails after the version release is created, upload that prepared appcast manually
with `gh release upload notch-updates build/distribution/appcast.xml --clobber
--repo dneethling/claud-o-meter` after inspecting the versioned release.

Before inviting users, test a real signed older-to-newer upgrade on your Mac:
confirm the new version installs, Keychain permission survives, and settings
persist. Signing/notarization and live update delivery have not been validated
from the Linux development environment. Keep versioned release assets available;
do not change signing keys casually. See [Sparkle documentation](https://sparkle-project.org/documentation/).
