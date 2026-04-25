# Claude Usage Widget (SwiftBar)

Menu bar widget that shows your Claude.ai session % and weekly % so you don't have to open Settings.

## What you're getting

- **Title in menu bar:** `🤖 17% · 14%w` (session · weekly)
- **Dropdown:** session %, session reset time, weekly all-models %, weekly Sonnet %, quick link to usage page
- **Refresh:** every 5 minutes (rename the file `.1m.sh` for every minute, `.10m.sh` for every 10, etc.)

## Install — 4 steps

### 1. Install SwiftBar
```bash
brew install --cask swiftbar
```
Launch SwiftBar. It will ask where your plugins folder is — point it at:
```
/Users/darrenneethling/Downloads/claude-usage-widget/plugins
```
(Or move the `plugins` folder somewhere more permanent first, e.g. `~/SwiftBarPlugins`, and point it there. Downloads gets messy.)

### 2. Install `jq` (JSON parser)
```bash
brew install jq
```

### 3. Grab your Claude endpoint + cookie (the one-time manual part)

1. Open https://claude.ai/settings/usage in Chrome (logged in).
2. Open DevTools: **Cmd+Opt+I** → **Network** tab.
3. Reload the page.
4. In the Network filter box, type `usage` or `rate` or `limit`. You're looking for a JSON XHR/fetch request whose response contains the percentages you see on screen. Click it to inspect.
5. **Copy the URL** (right-click the request → Copy → Copy URL).
6. **Copy the cookie:** right-click → Copy → Copy as cURL. Paste into a scratch doc, find the `-H 'Cookie: ...'` line, and copy *just the value* after `Cookie: `.

### 4. Fill in the config

First time the widget runs, it creates `~/.claude-usage-widget.conf` with placeholders. Open it:
```bash
open -t ~/.claude-usage-widget.conf
```
Paste your `USAGE_URL` and `COOKIE`. Save. Click the menu bar icon → **Refresh**.

## If the percentages don't show up

The jq paths in the script are educated guesses at Anthropic's JSON shape. First run dumps the raw response to `/tmp/claude-usage-raw.json`. Open it:
```bash
open -t /tmp/claude-usage-raw.json
```
Find the fields that match your session/weekly percentages, then edit the `jq` expressions near the bottom of `claude-usage.5m.sh`. Send me the JSON and I'll fix the paths for you.

## Caveats (read these)

- **Unofficial.** This scrapes the same endpoint the settings page uses. Anthropic can change it any time and the widget breaks silently. Not a big deal — you just redo step 3 + maybe tweak jq paths.
- **Cookie expires.** When your Claude session logs out, the cookie stops working. Redo step 6.
- **Personal use only.** Don't share your cookie, don't hammer the endpoint — 5-minute refresh is plenty.

## Files

- `plugins/claude-usage.5m.sh` — the widget
- `~/.claude-usage-widget.conf` — your endpoint + cookie (created on first run)
- `/tmp/claude-usage-raw.json` — last raw response, for debugging jq paths
