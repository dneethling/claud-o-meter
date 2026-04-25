# Claud-o-meter

A macOS menu bar app that shows your Claude.ai usage at a glance — session limits, weekly limits, and reset timers. No browser extensions, no config files.

## Install

1. Download **Claud-o-meter-v1.0.0.dmg** from [Releases](https://github.com/raptcreative/claud-o-meter/releases)
2. Open the DMG and drag **Claud-o-meter** to **Applications**
3. Open **Claud-o-meter** from Applications
   - First launch: macOS may say "unidentified developer." Right-click → Open → Open.
4. Sign in to Claude in the window that appears
5. Done — usage appears in your menu bar

## What You See

**Menu bar:** `🔘 12% · 8%w` — session usage and weekly usage at a glance.

**Click for details:**
- Session (5hr window) with progress bar and reset timer
- Weekly all-models usage
- Weekly Sonnet-specific usage
- Opus and extra usage (when applicable)

**Colors:**
- 🟢 Green: under 60%
- 🟠 Orange: 60–85%
- 🔴 Red: over 85% (you'll also get a notification)

## Features

- **Auto-refreshes** every 5 minutes
- **Notifications** when you're approaching limits (60% warning, 85% critical)
- **Launch at Login** enabled automatically — toggle in the menu
- **Log Out** to switch accounts

## Requirements

- macOS 13 (Ventura) or later
- A Claude Pro, Team, or Enterprise account

## Build from Source

```bash
cd Claud-o-meter
make app    # builds .app bundle
make dmg    # creates DMG for distribution
make run    # builds and runs directly
make clean  # removes build artifacts
```

Requires Xcode Command Line Tools (`xcode-select --install`).

## FAQ

**Q: Is this official?**
No. This is a community tool that reads your usage from claude.ai. Anthropic could change their API at any time.

**Q: Is my login safe?**
You're logging into claude.ai directly inside the app (same as using Safari). Your credentials are never sent anywhere else. Session cookies are stored locally in the app's sandboxed data.

**Q: My session expired — what do I do?**
The app will try to refresh automatically. If it can't, a login window will appear. Just sign in again.

**Q: How do I uninstall?**
Drag Claud-o-meter from Applications to Trash. To remove launch-at-login, go to System Settings → General → Login Items.
