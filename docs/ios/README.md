# Claude Meter — iOS Live Activity companion

Live Claude-usage meters in the **Dynamic Island** and on the **Lock Screen** of
your iPhone, so you can glance at your session/weekly limits while you code —
without opening `claude.ai/settings/usage`.

<p align="center"><em>Dynamic Island (compact):</em> <code>S 29%  ·  W 7%</code> &nbsp;→&nbsp; long-press to expand into rings, resets, and per-model limits.</p>

---

## How it works (and why it's built this way)

iOS does **not** allow an app to float a widget on top of other apps — Android
does, iOS doesn't. The Apple-sanctioned "live thing at the top of the screen" is
a **Live Activity** (the Dynamic Island pill + a Lock Screen card), so that's
what this is.

An iOS app is also sandboxed: it **cannot** read your Claude session cookie or
`~/.claude` logs the way the macOS menu-bar widget does. So the phone can't fetch
usage itself. Instead, a small **relay** on your computer fetches usage (reusing
this repo's existing `fetch_usage.py`) and pushes each update to the pill through
Apple Push Notification service (APNs).

```
  ┌─────────────────────────┐        HTTPS/2 + JWT        ┌───────────────┐
  │  ios_relay.py (Mac/PC)  │ ─────────────────────────▶ │     APNs      │
  │  • reuses fetch_usage   │                            │  (Apple)      │
  │  • maps → content-state │                            └──────┬────────┘
  │  • signs ES256 JWT      │                                   │ push
  └───────────▲─────────────┘                                   ▼
              │ registers its push token           ┌────────────────────────┐
              │  (HTTP on your LAN)                 │  iPhone Live Activity  │
              └──────────────────────────────────  │  Dynamic Island + Lock │
                                                    └────────────────────────┘
```

APNs is just HTTPS/2 + a signed token, so **the relay runs on macOS, Linux, or
Windows.** Only *building the app* needs a Mac with Xcode.

### Reality checks

- **You need a Mac with Xcode** to build and install the app. There is no way
  around this — iOS builds require Apple's toolchain.
- **Real-time push needs a paid Apple Developer Program membership** ($99/yr) to
  create the APNs Auth Key. (A free Apple ID can build/sideload the app, but
  can't do background push, so the pill would only update while the app is open.)
- **Unofficial, personal use.** Same caveat as the widget: this rides the
  internal usage endpoint via your own cookie. Don't share credentials.

---

## What you need

| | |
|---|---|
| A Mac with **Xcode 15+** | to build the app |
| An **Apple Developer Program** account | for the APNs Auth Key (push) |
| An iPhone on **iOS 17+** | Dynamic Island is iPhone 14 Pro and later; on older iPhones the Lock Screen card still works |
| Python 3.9+ on the machine that will run the relay | Mac, Linux, or Windows |
| A working `~/.claude-usage-widget.conf` | `USAGE_URL` + a valid `COOKIE` (see below) |

---

## Part A — Apple setup (once)

You'll collect four values. Keep them handy for Part C.

1. **Team ID** — [developer.apple.com/account](https://developer.apple.com/account)
   → *Membership details* → **Team ID** (10 characters).

2. **Bundle ID** — *Certificates, IDs & Profiles* → *Identifiers* → **+** →
   *App IDs* → *App*. Use `co.dcai.claudemeter` (or your own reverse-DNS id; if
   you change it, change it everywhere — `ios/project.yml`, the relay config, and
   the widget target). The widget extension's id must be that **plus `.widget`**,
   e.g. `co.dcai.claudemeter.widget`. Live Activities need no special capability
   toggle here — the `NSSupportsLiveActivities` key in the app is enough.

3. **APNs Auth Key (`.p8`)** — *Keys* → **+** → enable **Apple Push
   Notifications service (APNs)** → *Continue* → *Register*. **Download the
   `.p8`** — you can only download it **once**. Save it somewhere safe, e.g.
   `~/.claude-usage-AuthKey.p8`.

4. **Key ID** — shown next to that key (10 characters).

> One APNs key works for both sandbox and production and for all your apps.

---

## Part B — Build & install the app

The Xcode project is generated from `ios/project.yml` with
[XcodeGen](https://github.com/yonsson/XcodeGen):

```bash
brew install xcodegen
cd ios
xcodegen generate
open ClaudeMeter.xcodeproj
```

In Xcode:

1. Select the **ClaudeMeter** target → *Signing & Capabilities* → pick your
   **Team**. Do the same for the **ClaudeMeterWidgetExtension** target. (If you
   changed the bundle id in Part A, set it on both targets to match.)
2. Choose your iPhone as the run destination and press **Run** (⌘R). Trust the
   developer profile on the phone if prompted (*Settings › General › VPN & Device
   Management*).
3. The app installs. Leave it for Part D.

> **Prefer not to use XcodeGen?** Create a new iOS App project, add a *Widget
> Extension* target (uncheck "Include Configuration Intent"), then add the files
> from `ios/Shared`, `ios/ClaudeMeter`, and `ios/ClaudeMeterWidget` to the
> matching targets. `ios/Shared/*` must belong to **both** targets. Set
> `NSSupportsLiveActivities = YES` in the app's Info.plist.

---

## Part C — Configure & run the relay

On the machine that will stay awake while you code (your Mac, a home server, a
Raspberry Pi — anything on the same Wi-Fi as the phone):

```bash
cd /path/to/claud-o-meter
python3 -m venv .venv && . .venv/bin/activate      # Windows: .venv\Scripts\activate
pip install -r requirements.txt -r requirements-ios.txt

python ios_relay.py --init          # writes ~/.claude-usage-ios.conf (mode 600)
```

Edit `~/.claude-usage-ios.conf` with the four values from Part A:

```ini
APNS_TEAM_ID=ABCDE12345
APNS_KEY_ID=KEY1234567
APNS_P8=~/.claude-usage-AuthKey.p8
APNS_BUNDLE_ID=co.dcai.claudemeter
APNS_ENV=sandbox          # a Debug build from Xcode delivers via sandbox
POLL_SECONDS=30
```

> **sandbox vs production:** an app you **Run from Xcode** (Debug) gets a
> *sandbox* push token → keep `APNS_ENV=sandbox`. A **TestFlight or App Store**
> build gets a *production* token → set `APNS_ENV=production`. Using the wrong
> one gives `BadDeviceToken`.

**Usage source (`USAGE_URL` + `COOKIE`):** the relay reuses the widget's config.

- **macOS:** if you already run the menu-bar widget, this is done — and the relay
  will auto-refresh the cookie via your Keychain when it expires.
- **Windows / Linux:** there's no Keychain, so set it up manually once:
  ```ini
  # ~/.claude-usage-widget.conf   (mode 600)
  USAGE_URL=https://claude.ai/api/organizations/<YOUR-ORG-ID>/usage
  COOKIE=sessionKey=...; lastActiveOrg=...
  ```
  Get both from your browser: open `claude.ai/settings/usage`, DevTools →
  *Network* → click the `usage` request → copy the request URL and the `cookie`
  header. When that cookie expires you'll see a `reauth` state on the pill; paste
  a fresh cookie to recover. (A future enhancement could automate this per-OS.)

Sanity-check without touching Apple or your phone:

```bash
python ios_relay.py --self-test      # prints the exact payload the phone will get
python ios_relay.py --print-state    # fetches your real usage, prints the meters
```

Then run the relay:

```bash
python ios_relay.py
# [ios-relay] listening on http://0.0.0.0:8787  (push ON, poll 30s)
# [ios-relay] point the iOS app at  http://<this-machine-LAN-IP>:8787
```

Find `<this-machine-LAN-IP>`: macOS `ipconfig getifaddr en0`, Linux `hostname -I`,
Windows `ipconfig`.

**Keep it running:** on macOS you can adapt the existing
`com.darren.claude-usage-refresh.plist` LaunchAgent, or just run it in a terminal
/ `tmux`. On Linux, a `systemd --user` service; on Windows, Task Scheduler.

---

## Part D — Connect the phone

1. Open **Claude Meter** on the iPhone (same Wi-Fi as the relay).
2. Enter the relay **Host/IP** and **Port** (8787). Tap **Test connection** — you
   should see *Connected · push on*.
3. Tap **Start the meter**. iOS asks for local-network permission the first time —
   allow it. The Live Activity appears; within a few seconds the relay pushes real
   numbers and the rings fill in.
4. Long-press the Dynamic Island to expand it; check the Lock Screen too.

That's it. The relay pushes updates every `POLL_SECONDS` (and immediately when a
number changes), even while the app is closed, until you tap **Stop** or the
activity ages out (iOS ends Live Activities after several hours — just tap Start
again).

---

## Troubleshooting

| Symptom | Cause / fix |
|---|---|
| `BadDeviceToken` in the relay log | `APNS_ENV` doesn't match the build. Xcode Debug → `sandbox`; TestFlight/App Store → `production`. |
| `InvalidProviderToken` / `403` | Wrong `APNS_TEAM_ID`/`APNS_KEY_ID`, or the `.p8` isn't an APNs key. Re-check Part A. |
| `TopicDisallowed` / `DeviceTokenNotForTopic` | `APNS_BUNDLE_ID` doesn't match the app's bundle id. |
| Pill starts but never fills in | Relay can't fetch usage. Run `python ios_relay.py --print-state` — a `reauth` status means the cookie is stale/missing. |
| "Test connection" fails | Phone and relay aren't on the same network, wrong IP/port, or a firewall blocks the port. On macOS allow incoming connections for Python. |
| Local-network prompt never came / was denied | *Settings › Claude Meter › Local Network* → on. |
| Pill shows but is greyed/"stale" | The relay stopped pushing (process died / laptop slept). Restart `ios_relay.py`. |
| Pushes work but HTTP/1.1 error from APNs | `pip install "httpx[http2]"` and re-run — APNs requires HTTP/2. |

Verbose relay logging goes to stderr. `python ios_relay.py --list-tokens` shows
each registered device and the last APNs status/reason.

---

## Security notes

- `~/.claude-usage-ios.conf`, `*.p8`, and `~/.claude-usage-ios-tokens.json` are
  **gitignored** and never committed. The `.p8` is a signing secret — treat it
  like a password.
- The relay's HTTP endpoint is **LAN-only** and holds no secrets, but any device
  on your network could register a token. Set `RELAY_SECRET` in the config (and
  the same value in the app's *Shared secret* field) to lock it down.
- The push token is not sensitive on its own — it's only useful with your `.p8`.

---

## Files

| Path | What it is |
|---|---|
| `ios_relay.py` | The cross-platform relay (fetch → map → push; LAN token receiver) |
| `lib/usage_state.py` | Pure mapping: raw Claude usage JSON → Live Activity content-state |
| `lib/apns.py` | ES256 JWT + APNs Live Activity push client |
| `ios/project.yml` | XcodeGen definition (app + widget-extension targets) |
| `ios/Shared/` | `ActivityAttributes`/`ContentState` + colour mapping (both targets) |
| `ios/ClaudeMeter/` | The app (setup UI, Live Activity controller, relay client) |
| `ios/ClaudeMeterWidget/` | The Live Activity UI (Dynamic Island + Lock Screen) |
| `requirements-ios.txt` | Relay-only Python deps (`cryptography`, `httpx[http2]`) |
