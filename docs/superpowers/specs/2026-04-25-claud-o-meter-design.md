# Claud-o-meter — Design Spec

**Date:** 2026-04-25
**Status:** Approved
**Author:** Darren Neethling + Claude

## Overview

A native macOS menu bar app that shows live Claude.ai usage (session %, weekly %, Sonnet %, reset timers) with color-coded progress bars and threshold notifications. Users install, log in once inside the app, and it runs forever — no config files, no browser extensions, no terminal.

## Target User

Anyone with a Claude Pro, Team, or Enterprise plan on macOS 13+. Non-technical. Expects a standard Mac app experience: open DMG, drag to Applications, launch, log in, done.

## Architecture

Pure Swift, SwiftUI for the login window, AppKit (NSStatusItem) for the menu bar. Single `.app` bundle with zero external dependencies.

### Components

#### 1. ClaudOMeterApp (entry point)
- `@main` App struct, `LSUIElement = true` (menu bar only, no dock icon).
- Creates the `NSStatusItem` and owns the `AuthManager`, `UsageFetcher`, and `NotificationManager`.

#### 2. AuthManager
- Owns a persistent `WKWebView` backed by a named `WKWebsiteDataStore` (cookies survive app restarts).
- On first launch or session expiry, presents a `NSWindow` containing the WKWebView, loading `https://claude.ai/login`.
- Monitors navigation via `WKNavigationDelegate`. Auth is considered complete when the URL path leaves `/login` (e.g. navigates to `/new`, `/settings`, `/chat/*`).
- On auth complete:
  - Reads `lastActiveOrg` cookie from `WKHTTPCookieStore` → derives org ID.
  - If cookie missing, fetches `https://claude.ai/api/auth/session` via JS `fetch()` inside the webview and parses org ID from the response.
  - Stores org ID in `UserDefaults`.
  - Closes the login window.
  - Notifies `UsageFetcher` to start polling.

#### 3. UsageFetcher
- Fetches usage by evaluating JavaScript `fetch()` inside the WKWebView (uses WebKit's TLS stack → passes Cloudflare).
- Endpoint: `GET https://claude.ai/api/organizations/{orgId}/usage`
- Polls every 5 minutes via `Timer.scheduledTimer`.
- Parses JSON response into a `UsageData` struct:
  ```
  UsageData {
    session: Metric?        // five_hour
    weeklyAll: Metric?      // seven_day
    weeklySonnet: Metric?   // seven_day_sonnet
    weeklyOpus: Metric?     // seven_day_opus
    extraUsage: ExtraUsage? // extra_usage
  }
  Metric {
    utilization: Double     // e.g. 24.0
    resetsAt: Date?
  }
  ExtraUsage {
    isEnabled: Bool
    utilization: Double?
  }
  ```
- On HTTP 401/403 or non-JSON response: triggers `AuthManager` re-auth flow.
- On network error: retries with exponential backoff (30s, 60s, 120s), then shows error state in menu bar.

#### 4. MenuBarController
- Manages `NSStatusItem` with `NSStatusBarButton`.
- **Title format:** `12% · 8%w` (session · weekly).
- **Icon:** SF Symbol — `gauge.with.dots.needle.33percent` (green, <60%), `gauge.with.dots.needle.67percent` (orange, 60-85%), `bolt.trianglebadge.exclamationmark` (red, ≥85%).
- **Color:** green `#34C759` / orange `#FF9500` / red `#FF3B30` based on session utilization.
- **Dropdown menu** (NSMenu) structure:
  ```
  ┌──────────────────────────────────────────┐
  │ Claud-o-meter                    v1.0.0  │
  │ ──────────────────────────────────────── │
  │ SESSION (5hr window)                     │
  │ ████████░░░░░░░░░░░░  24%               │
  │ Resets in 2h 14m                         │
  │ ──────────────────────────────────────── │
  │ WEEKLY · ALL MODELS                      │
  │ ██░░░░░░░░░░░░░░░░░░  8%                │
  │ Resets Mon 3:00pm                        │
  │ ──────────────────────────────────────── │
  │ WEEKLY · SONNET                          │
  │ █░░░░░░░░░░░░░░░░░░░  3%                │
  │ Resets Mon 4:00pm                        │
  │ ──────────────────────────────────────── │
  │ ↻ Refresh Now                            │
  │ ☑ Launch at Login                        │
  │ ──────────────────────────────────────── │
  │ Log Out                                  │
  │ Quit Claud-o-meter                       │
  └──────────────────────────────────────────┘
  ```
- Progress bars rendered as `NSAttributedString` with monospaced font (Menlo 12pt) and color.
- "Resets in Xh Ym" for <24h, "Mon 3:00pm" for >24h.
- Menu items for Opus and Extra Usage shown conditionally (only when present in API response).

#### 5. NotificationManager
- Uses `UNUserNotificationCenter`.
- Requests notification permission on first successful fetch.
- Fires notification when session crosses 60% (warning) or 85% (critical).
- Tracks last-fired threshold in memory to avoid re-firing. Resets when utilization drops below the threshold.

#### 6. LaunchAtLoginManager
- Uses `SMAppService.loginItem` (macOS 13+).
- Enabled automatically after first successful login.
- Togglable via menu item "Launch at Login" (checkmark state reflects current registration).
- Persists naturally through the system's Login Items — no UserDefaults needed.

### Data Flow

```
Timer (every 5m)
  → UsageFetcher.fetch()
    → WKWebView.evaluateJavaScript("fetch('/api/organizations/{org}/usage').then(r => r.text())")
    → Parse JSON → UsageData
    → MenuBarController.update(usageData)
    → NotificationManager.check(usageData)

Auth expired (401/403 from fetch)
  → AuthManager.reauth()
    → Try silent WKWebView reload of claude.ai first
    → If cookies still valid → resume polling
    → If not → show login window
```

### Error States

| State | Menu bar shows | Action |
|-------|---------------|--------|
| Not logged in | `⚠ Log in` (orange) | Click opens login window |
| Fetch failed (network) | `✖ Offline` (red) | Auto-retries with backoff |
| Session expired | `⚠ Session expired` (orange) | Auto-attempts silent refresh, then shows login |
| Fetching | Last known values | Shows spinner briefly on manual refresh |

## Auth Flow Detail

```
App Launch
  ├─ Has stored orgId + WKWebView has persistent cookies?
  │   ├─ YES → Attempt fetch immediately
  │   │   ├─ Success → Show usage, start polling
  │   │   └─ Fail (401) → Silent reload claude.ai in WKWebView
  │   │       ├─ Cookies refreshed → retry fetch → success
  │   │       └─ Still 401 → Show login window
  │   └─ NO → Show login window
  │
  Login Window
    → WKWebView loads https://claude.ai/login
    → User logs in (email, Google, SSO — all work natively in WKWebView)
    → WKNavigationDelegate detects URL leaves /login path
    → Read lastActiveOrg from WKHTTPCookieStore
    → Store orgId in UserDefaults
    → Close window, enable Launch at Login, start polling
```

## Distribution

- **Xcode project** with Swift Package Manager (no CocoaPods/Carthage).
- Minimum deployment target: **macOS 13.0** (Ventura) — required for `SMAppService`.
- `Info.plist`:
  - `LSUIElement = true` (no dock icon)
  - `NSAppTransportSecurity` → allow claude.ai
  - Bundle ID: `com.claudometer.app`
  - App name: `Claud-o-meter`
- Build produces `Claud-o-meter.app`.
- Packaged as **DMG** with background image, drag-to-Applications arrow.
- Hosted on **GitHub Releases** for download.
- Code-signed with Developer ID (if available) or unsigned with instructions to right-click → Open on first launch.

## File Structure

```
Claud-o-meter/
├── Claud-o-meter.xcodeproj/
├── Claud-o-meter/
│   ├── ClaudOMeterApp.swift          # @main entry, app lifecycle
│   ├── MenuBarController.swift       # NSStatusItem, menu building, progress bars
│   ├── AuthManager.swift             # WKWebView, login window, cookie management
│   ├── UsageFetcher.swift            # JS fetch via WKWebView, JSON parsing
│   ├── NotificationManager.swift     # UNUserNotificationCenter alerts
│   ├── LaunchAtLoginManager.swift    # SMAppService.loginItem wrapper
│   ├── Models/
│   │   └── UsageData.swift           # UsageData, Metric, ExtraUsage structs
│   ├── Views/
│   │   └── LoginWindow.swift         # NSWindow + WKWebView for auth
│   ├── Utilities/
│   │   ├── ProgressBar.swift         # Unicode bar rendering
│   │   └── TimeFormatter.swift       # "in 2h 14m" / "Mon 3:00pm" formatting
│   ├── Assets.xcassets/
│   │   └── AppIcon.appiconset/       # App icon
│   ├── Info.plist
│   └── Claud_o_meter.entitlements    # Outgoing network, keychain (if needed)
├── DMG/
│   ├── background.png                # DMG background with arrow
│   └── create-dmg.sh                 # Script to build DMG from .app
└── README.md
```

## What's NOT in v1

- No usage history or charts
- No team/org-wide aggregate view
- No Homebrew formula
- No App Store distribution
- No multi-org switcher (uses lastActiveOrg)
- No custom refresh intervals (hardcoded 5 min)
