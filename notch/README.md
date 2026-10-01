# Claud-o-meter Notch — comparison edition

A separate macOS 26+ app based on [Codenotch](https://github.com/vinzdg/codenotch).
The SwiftBar edition, its installer, configuration and history are untouched.
Run both at once to compare. Native compilation and live credentials require a Mac.

## Try it

Install full Xcode 26+ and XcodeGen (`brew install xcodegen`). In a **separate** checkout:

```sh
git clone --branch codex/notch-comparison https://github.com/dneethling/claud-o-meter.git ~/claud-o-meter-notch
cd ~/claud-o-meter-notch/notch
make install
```

This installs `ClaudOMeterNotch.app` in `~/Applications`. It does not stop SwiftBar
or change `~/claud-o-meter`. For a visual preview using sample values, use
`make demo` instead. Demo mode does not read credentials or monitor sessions.
Local builds are ad-hoc signed, not notarized distribution builds.

Claude and Codex are enabled initially. Other inherited providers are opt-in in
Settings. The notch supports four edges, hover details, reset countdowns and
session activity. Existing SwiftBar predictions, export and threshold alerts
have **not** been ported into the notch edition.

## Browser-free authentication

Sign into Claude Code with your subscription account and approve macOS Keychain
access for this app. It reads the existing OAuth access token, not browser cookies.
The app never refreshes or overwrites Claude Code's credential. If it expires,
use Claude Code again so it can refresh; if revoked, sign back in there.
The usage endpoint is internal, not a supported third-party subscription API.

Codex uses its local app-server interface, with rollout-log fallback. Readings
from a different account or an older poll may differ from SwiftBar: compare the
same account, limit window and timestamp. Local token counts are not quota percentages.

Settings and last-good readings use `com.darren.claudometer.notch`, separate
from both SwiftBar and upstream Codenotch. Rebuilding ad-hoc signed binaries can
cause another Keychain prompt; stable Developer ID signing is the distribution path.

## Validation and releases

`make test` runs the inherited native provider, layout and session tests, plus
comparison-isolation checks. The GitHub Notch CI workflow builds and tests on macOS.
No live account is required for its tests. A live Mac check is still needed for
Keychain permissions, displayed quota values, sleep/wake and multi-monitor placement.

See [RELEASING.md](RELEASING.md) for publishing signed automatic updates. Local
builds have the update channel disabled until your public signing key is supplied.
Upstream attribution and pinned source commit: [UPSTREAM.md](UPSTREAM.md).
