# Claude Usage Widget (SwiftBar)

Menu bar widget that shows your Claude.ai session %, weekly %, Sonnet % and Opus % so you don't have to open Settings.

## What you're getting

- **Title in menu bar:** `🤖 29% · 7%w` (session · weekly), colour-coded green/orange/red as you approach limits
- **Dropdown:** progress bar + reset time per metric (session, weekly all-models, weekly Sonnet, weekly Opus, extra usage if enabled)
- **Notifications:** macOS Notification Centre alert at 60% (warning) and 85% (critical), de-duplicated so you only get one per crossing
- **Auto cookie refresh:** pulls a fresh session cookie from Arc / Chrome / Brave on a 30-min schedule and on every failed fetch — no manual copy-paste, no expiry surprises
- **Refresh:** every 5 minutes (rename the file `.1m.sh` for every minute, `.10m.sh` for every 10, etc.)

## How the auth flow works (so the magic isn't a mystery)

There's no public API for Claude usage. The widget hits the same internal endpoint that `claude.ai/settings/usage` uses, with your browser's session cookie.

1. `refresh_cookie.py` reads cookies directly from the cookie DBs of every Chromium-family browser you have (Arc, Chrome, Brave, Chromium), decrypts them with the per-browser Safe Storage key from your macOS Keychain, and **tests each candidate against the live Claude API**. The first one that returns a real JSON usage response gets written to `~/.claude-usage-widget.conf`.
2. `fetch_usage.py` uses the cookie via `curl_cffi` (Chrome TLS fingerprint, so Cloudflare passes), parses the JSON, writes it to stdout. Refuses to write garbage on failures.
3. The SwiftBar plugin reads, parses with `jq`, and renders.

The "test-each-cookie-against-the-API" step is what stops a stale session in Chrome Profile 2 from drowning out a valid session in Arc Profile 5.

## Install

### 1. Tools

```bash
brew install --cask swiftbar
brew install jq
```

Then create a Python venv and install the two deps:

```bash
cd /path/to/claude-usage-widget
python3 -m venv .venv
.venv/bin/pip install curl_cffi pycookiecheat
```

### 2. Point SwiftBar at the repo's `plugins/` directory

Launch SwiftBar → Preferences → Plugin Folder → choose `/path/to/claude-usage-widget/plugins`. **Do not copy or symlink the plugin file** — let SwiftBar read it from the repo so updates land instantly.

If you previously copied it into `~/Library/Application Support/SwiftBar/Plugins/`, delete that copy (or any empty directories with the plugin name) so they don't shadow the real one.

### 3. Make sure you're logged into Claude in any Chromium-family browser

Open `claude.ai` in Arc / Chrome / Brave and sign in once. That's it. The refresher will find it.

### 4. (Optional) Install the periodic cookie-refresh job

Cookies do rotate, so a launch agent runs `refresh_cookie.py` every 30 minutes in the background.

Edit `com.darren.claude-usage-refresh.plist` to point at your venv + script paths, then:

```bash
cp com.darren.claude-usage-refresh.plist ~/Library/LaunchAgents/
launchctl bootstrap gui/$(id -u) ~/Library/LaunchAgents/com.darren.claude-usage-refresh.plist
launchctl list | grep claude-usage
```

The plugin also calls `refresh_cookie.py` itself whenever a fetch fails, so even without the launch agent things keep working — the agent just keeps the cookie fresh proactively so you never see a momentary error tile.

### 5. First run

Click the SwiftBar icon → Refresh. You should see something like `29% · 7%w` in green.

### Menu-bar display modes

By default the menu bar shows Claude limits only: `16% · 10%w`. To also surface
local token volume, add one line to `~/.claude-usage-widget.conf`:

    MENUBAR_MODE=codex   # adds Codex 30-day tokens:  16% · 10%w · cx 132M
    MENUBAR_MODE=both    # adds Claude Code too:       16% · 10%w · cc 22.5B · cx 132M

Remove the line (or set `claude`) for the default.

### Colour themes

Add a `THEME=` line to the config to change how percentages are coloured:

    THEME=semantic     # default: green / orange / red
    THEME=colorblind   # Okabe-Ito blue / orange / vermillion (red-green safe)
    THEME=minimal      # monochrome: no traffic-light colours, just numbers and bars

Errors and incidents always keep their warning colour regardless of theme.

## What each menu-bar state means

| Title shows | What it means | What to do |
|---|---|---|
| `29% · 7%w` (green) | Session 29%, weekly 7%. Everything's fine. | Nothing. |
| Orange gauge / 60-84% | Session approaching limit. You also got a notification. | Maybe slow down. |
| Red bolt / 85%+ | Critical. Another notification. | Wait for reset, see dropdown. |
| `⚠ Re-auth` (orange) | No valid Claude session anywhere — you logged out everywhere. | Log into `claude.ai` in your browser; the widget recovers on its own within 5 min. |
| `✖ Claude` (red) | Network / Cloudflare / something transient. | Click → View error log. Usually self-heals next tick. |
| `?%` (orange `?` icon) | Fetch succeeded but the JSON shape doesn't match — Anthropic likely renamed a field. | Click → View raw JSON. Send the snippet to whoever maintains your fork so they can fix the `jq` paths. |

## Troubleshooting

### Logs
- `/tmp/claude-usage-err.log` — last stderr from fetch / refresh
- `/tmp/claude-usage-refresh.log` — stdout of the launchd-driven refresher
- `/tmp/claude-usage-refresh.err` — stderr of same (this is where you look when something is wrong)
- `/tmp/claude-usage-raw.json` — last successful API response (only overwritten on success — so failures don't poison it)

All logs auto-rotate to `.1` when they exceed 1 MB.

### Force a refresh manually

```bash
CLAUDE_USAGE_VERBOSE=1 ./.venv/bin/python refresh_cookie.py
```

You'll see which browser/profile it picked, or which ones it tried and rejected.

### See exactly what the API is returning

```bash
./.venv/bin/python fetch_usage.py | jq .
```

Exits 1 with the reason on stderr if it can't get a clean JSON 200.

### "I'm logged in but the widget says Re-auth"

Run the verbose refresh above. The reason for each rejected browser is printed — common ones: `account_session_invalid` (that profile is logged out), `decrypt failed` (Keychain access denied), `missing required` (Claude was never opened in that profile).

### macOS Notifications never appear

Allow notifications for `osascript` or `Script Editor` in System Settings → Notifications.

## Caveats (read these)

- **Unofficial.** This uses the same endpoint the settings page uses. Anthropic can change it any time and the widget breaks — when that happens you'll see `?%` in the title, not silence.
- **Personal use only.** Don't share your cookies. 5-minute polling is plenty.
- **Browser cookie access requires Keychain permission** the first time the refresher runs. Approve the prompt for each browser you use with Claude.

## Files

| File | What it does |
|---|---|
| `plugins/claude-usage.5m.sh` | SwiftBar plugin (entry point) |
| `fetch_usage.py` | One-shot HTTP fetch with Cloudflare bypass |
| `refresh_cookie.py` | Pull + validate + write fresh cookies |
| `com.darren.claude-usage-refresh.plist` | LaunchAgent for the 30-min refresh job |
| `~/.claude-usage-widget.conf` | Endpoint + current cookie (created on first refresh) — mode 600 |
| `~/.claude-usage-widget.conf.lock` | Empty lock file used by atomic writes |
| `/tmp/claude-usage-*.log` | Logs (see Troubleshooting) |
