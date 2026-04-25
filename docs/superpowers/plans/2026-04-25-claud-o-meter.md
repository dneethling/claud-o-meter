# Claud-o-meter Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Build a native macOS menu bar app that shows live Claude.ai usage with in-app WKWebView login, color-coded progress bars, threshold notifications, and launch-at-login support.

**Architecture:** Pure Swift macOS app. WKWebView handles both auth (login page) and data fetching (JS `fetch()` bypasses Cloudflare). NSStatusItem renders the menu bar UI. No external dependencies.

**Tech Stack:** Swift 6+, AppKit (NSStatusItem/NSMenu), WebKit (WKWebView), UserNotifications, ServiceManagement (SMAppService)

---

## File Structure

```
Claud-o-meter/                              # Project root (inside claude-usage-widget/)
├── Package.swift                            # SPM executable package (no .xcodeproj needed)
├── Sources/
│   ├── ClaudOMeterApp.swift                 # @main, NSApplication lifecycle, wires components
│   ├── MenuBarController.swift              # NSStatusItem, NSMenu, progress bar rendering
│   ├── AuthManager.swift                    # WKWebView, login window, cookie/org extraction
│   ├── UsageFetcher.swift                   # JS fetch() via WKWebView, JSON → UsageData
│   ├── NotificationManager.swift            # UNUserNotificationCenter threshold alerts
│   ├── LaunchAtLoginManager.swift           # SMAppService.loginItem wrapper
│   ├── UsageData.swift                      # Codable structs for API response
│   ├── ProgressBar.swift                    # Unicode bar rendering utility
│   ├── TimeFormatter.swift                  # "in 2h 14m" / "Mon 3:00pm" formatting
│   └── LoginWindow.swift                    # NSWindow subclass hosting WKWebView
├── Resources/
│   └── Info.plist                           # LSUIElement, bundle ID, ATS config
├── Tests/
│   ├── UsageDataTests.swift                 # JSON parsing tests
│   ├── ProgressBarTests.swift               # Bar rendering tests
│   └── TimeFormatterTests.swift             # Time formatting tests
├── DMG/
│   └── create-dmg.sh                        # Build DMG from .app
└── README.md                                # User-facing install + usage docs
```

**Why SPM over .xcodeproj:** `swift build` works from the command line without opening Xcode. The agent can build, test, and iterate entirely from the terminal. `xcodebuild` can still open it if needed (`swift package generate-xcodeproj` or `open Package.swift`).

---

### Task 1: Project Scaffold + Build Verification

**Files:**
- Create: `Claud-o-meter/Package.swift`
- Create: `Claud-o-meter/Sources/ClaudOMeterApp.swift`
- Create: `Claud-o-meter/Resources/Info.plist`

- [ ] **Step 1: Create Package.swift**

```swift
// Claud-o-meter/Package.swift
// swift-tools-version: 5.9
import PackageDescription

let package = Package(
    name: "Claud-o-meter",
    platforms: [.macOS(.v13)],
    targets: [
        .executableTarget(
            name: "Claud-o-meter",
            path: "Sources",
            resources: [
                .copy("../Resources/Info.plist")
            ]
        ),
        .testTarget(
            name: "ClaudOMeterTests",
            dependencies: ["Claud-o-meter"],
            path: "Tests"
        )
    ]
)
```

- [ ] **Step 2: Create minimal app entry point**

```swift
// Claud-o-meter/Sources/ClaudOMeterApp.swift
import AppKit

@main
struct ClaudOMeterApp {
    static func main() {
        let app = NSApplication.shared
        app.setActivationPolicy(.accessory) // no dock icon
        
        let delegate = AppDelegate()
        app.delegate = delegate
        app.run()
    }
}

class AppDelegate: NSObject, NSApplicationDelegate {
    private var statusItem: NSStatusItem!
    
    func applicationDidFinishLaunching(_ notification: Notification) {
        statusItem = NSStatusBar.system.statusItem(withLength: NSStatusItem.variableLength)
        if let button = statusItem.button {
            button.image = NSImage(systemSymbolName: "gauge.with.dots.needle.33percent", accessibilityDescription: "Claude Usage")
            button.title = " --% · --%w"
        }
        
        let menu = NSMenu()
        menu.addItem(NSMenuItem(title: "Claud-o-meter v1.0.0", action: nil, keyEquivalent: ""))
        menu.addItem(NSMenuItem.separator())
        menu.addItem(NSMenuItem(title: "Quit", action: #selector(NSApplication.terminate(_:)), keyEquivalent: "q"))
        statusItem.menu = menu
    }
}
```

- [ ] **Step 3: Create Info.plist**

```xml
<?xml version="1.0" encoding="UTF-8"?>
<!DOCTYPE plist PUBLIC "-//Apple//DTD PLIST 1.0//EN" "http://www.apple.com/DTDs/PropertyList-1.0.dtd">
<plist version="1.0">
<dict>
    <key>CFBundleName</key>
    <string>Claud-o-meter</string>
    <key>CFBundleIdentifier</key>
    <string>com.claudometer.app</string>
    <key>CFBundleVersion</key>
    <string>1.0.0</string>
    <key>CFBundleShortVersionString</key>
    <string>1.0.0</string>
    <key>LSUIElement</key>
    <true/>
    <key>LSMinimumSystemVersion</key>
    <string>13.0</string>
    <key>NSAppTransportSecurity</key>
    <dict>
        <key>NSAllowsArbitraryLoads</key>
        <false/>
        <key>NSExceptionDomains</key>
        <dict>
            <key>claude.ai</key>
            <dict>
                <key>NSIncludesSubdomains</key>
                <true/>
                <key>NSThirdPartyExceptionAllowsInsecureHTTPLoads</key>
                <false/>
            </dict>
        </dict>
    </dict>
</dict>
</plist>
```

- [ ] **Step 4: Build and verify**

Run from `Claud-o-meter/`:
```bash
swift build 2>&1
```
Expected: `Build complete!` with no errors.

- [ ] **Step 5: Run to verify menu bar icon appears**

```bash
swift run &
sleep 3
# Verify: a gauge icon with "--% · --%w" appears in the menu bar
# Click it: shows "Claud-o-meter v1.0.0" and "Quit"
kill %1
```

- [ ] **Step 6: Commit**

```bash
git add Claud-o-meter/
git commit -m "feat: scaffold Claud-o-meter Swift package with menu bar stub"
```

---

### Task 2: Data Models + JSON Parsing

**Files:**
- Create: `Claud-o-meter/Sources/UsageData.swift`
- Create: `Claud-o-meter/Tests/UsageDataTests.swift`

- [ ] **Step 1: Write failing tests for JSON parsing**

```swift
// Claud-o-meter/Tests/UsageDataTests.swift
import XCTest
@testable import Claud_o_meter

final class UsageDataTests: XCTestCase {
    
    let sampleJSON = """
    {
        "five_hour": {"utilization": 24.0, "resets_at": "2026-04-14T14:59:59.793717+00:00"},
        "seven_day": {"utilization": 14.0, "resets_at": "2026-04-20T13:00:00.793738+00:00"},
        "seven_day_oauth_apps": null,
        "seven_day_opus": null,
        "seven_day_sonnet": {"utilization": 2.0, "resets_at": "2026-04-20T14:00:00.793747+00:00"},
        "seven_day_cowork": null,
        "seven_day_omelette": {"utilization": 0.0, "resets_at": null},
        "iguana_necktie": null,
        "extra_usage": {"is_enabled": false, "monthly_limit": null, "used_credits": null, "utilization": null, "currency": null}
    }
    """.data(using: .utf8)!
    
    func testDecodesSessionMetric() throws {
        let usage = try JSONDecoder.apiDecoder.decode(UsageData.self, from: sampleJSON)
        XCTAssertEqual(usage.session?.utilization, 24.0)
        XCTAssertNotNil(usage.session?.resetsAt)
    }
    
    func testDecodesWeeklyAllModels() throws {
        let usage = try JSONDecoder.apiDecoder.decode(UsageData.self, from: sampleJSON)
        XCTAssertEqual(usage.weeklyAll?.utilization, 14.0)
    }
    
    func testDecodesSonnet() throws {
        let usage = try JSONDecoder.apiDecoder.decode(UsageData.self, from: sampleJSON)
        XCTAssertEqual(usage.weeklySonnet?.utilization, 2.0)
    }
    
    func testDecodesNullOpusAsNil() throws {
        let usage = try JSONDecoder.apiDecoder.decode(UsageData.self, from: sampleJSON)
        XCTAssertNil(usage.weeklyOpus)
    }
    
    func testDecodesExtraUsageDisabled() throws {
        let usage = try JSONDecoder.apiDecoder.decode(UsageData.self, from: sampleJSON)
        XCTAssertEqual(usage.extraUsage?.isEnabled, false)
        XCTAssertNil(usage.extraUsage?.utilization)
    }
    
    func testDecodesNullResetsAtAsNil() throws {
        let usage = try JSONDecoder.apiDecoder.decode(UsageData.self, from: sampleJSON)
        // seven_day_omelette has resets_at: null — we don't map omelette but test the pattern
        // Use session which has a non-null resets_at
        XCTAssertNotNil(usage.session?.resetsAt)
    }
    
    func testHighestUtilization() throws {
        let usage = try JSONDecoder.apiDecoder.decode(UsageData.self, from: sampleJSON)
        XCTAssertEqual(usage.highestUtilization, 24.0) // session is highest
    }
}
```

- [ ] **Step 2: Run tests to verify they fail**

```bash
cd Claud-o-meter && swift test --filter UsageDataTests 2>&1
```
Expected: compilation error — `UsageData` not defined.

- [ ] **Step 3: Implement UsageData model**

```swift
// Claud-o-meter/Sources/UsageData.swift
import Foundation

struct Metric: Codable, Sendable {
    let utilization: Double
    let resetsAt: Date?
    
    enum CodingKeys: String, CodingKey {
        case utilization
        case resetsAt = "resets_at"
    }
}

struct ExtraUsage: Codable, Sendable {
    let isEnabled: Bool
    let monthlyLimit: Double?
    let usedCredits: Double?
    let utilization: Double?
    
    enum CodingKeys: String, CodingKey {
        case isEnabled = "is_enabled"
        case monthlyLimit = "monthly_limit"
        case usedCredits = "used_credits"
        case utilization
    }
}

struct UsageData: Codable, Sendable {
    let session: Metric?
    let weeklyAll: Metric?
    let weeklySonnet: Metric?
    let weeklyOpus: Metric?
    let extraUsage: ExtraUsage?
    
    enum CodingKeys: String, CodingKey {
        case session = "five_hour"
        case weeklyAll = "seven_day"
        case weeklySonnet = "seven_day_sonnet"
        case weeklyOpus = "seven_day_opus"
        case extraUsage = "extra_usage"
    }
    
    /// Returns the highest utilization across all non-nil metrics.
    var highestUtilization: Double {
        [session?.utilization, weeklyAll?.utilization, weeklySonnet?.utilization, weeklyOpus?.utilization]
            .compactMap { $0 }
            .max() ?? 0
    }
}

extension JSONDecoder {
    /// Decoder configured for the Claude API response format.
    static let apiDecoder: JSONDecoder = {
        let decoder = JSONDecoder()
        let formatter = ISO8601DateFormatter()
        formatter.formatOptions = [.withInternetDateTime, .withFractionalSeconds]
        let fallbackFormatter = ISO8601DateFormatter()
        fallbackFormatter.formatOptions = [.withInternetDateTime]
        decoder.dateDecodingStrategy = .custom { decoder in
            let container = try decoder.singleValueContainer()
            let string = try container.decode(String.self)
            if let date = formatter.date(from: string) { return date }
            if let date = fallbackFormatter.date(from: string) { return date }
            throw DecodingError.dataCorruptedError(in: container, debugDescription: "Invalid date: \(string)")
        }
        return decoder
    }()
}
```

- [ ] **Step 4: Run tests to verify they pass**

```bash
cd Claud-o-meter && swift test --filter UsageDataTests 2>&1
```
Expected: all 7 tests PASS.

- [ ] **Step 5: Commit**

```bash
git add Claud-o-meter/Sources/UsageData.swift Claud-o-meter/Tests/UsageDataTests.swift
git commit -m "feat: add UsageData model with Codable JSON parsing"
```

---

### Task 3: Utility — Progress Bar Rendering

**Files:**
- Create: `Claud-o-meter/Sources/ProgressBar.swift`
- Create: `Claud-o-meter/Tests/ProgressBarTests.swift`

- [ ] **Step 1: Write failing tests**

```swift
// Claud-o-meter/Tests/ProgressBarTests.swift
import XCTest
@testable import Claud_o_meter

final class ProgressBarTests: XCTestCase {
    
    func testZeroPercent() {
        let bar = ProgressBar.render(percent: 0, width: 10)
        XCTAssertEqual(bar, "░░░░░░░░░░")
    }
    
    func testHundredPercent() {
        let bar = ProgressBar.render(percent: 100, width: 10)
        XCTAssertEqual(bar, "██████████")
    }
    
    func testFiftyPercent() {
        let bar = ProgressBar.render(percent: 50, width: 10)
        XCTAssertEqual(bar, "█████░░░░░")
    }
    
    func testOverHundredClamps() {
        let bar = ProgressBar.render(percent: 150, width: 10)
        XCTAssertEqual(bar, "██████████")
    }
    
    func testNegativeClamps() {
        let bar = ProgressBar.render(percent: -10, width: 10)
        XCTAssertEqual(bar, "░░░░░░░░░░")
    }
    
    func testDefaultWidth() {
        let bar = ProgressBar.render(percent: 50)
        XCTAssertEqual(bar.count, 20) // default width is 20
    }
    
    func testColorForPercentGreen() {
        let color = ProgressBar.color(for: 30)
        XCTAssertEqual(color, .green)
    }
    
    func testColorForPercentOrange() {
        let color = ProgressBar.color(for: 65)
        XCTAssertEqual(color, .orange)
    }
    
    func testColorForPercentRed() {
        let color = ProgressBar.color(for: 90)
        XCTAssertEqual(color, .red)
    }
}
```

- [ ] **Step 2: Run tests to verify they fail**

```bash
cd Claud-o-meter && swift test --filter ProgressBarTests 2>&1
```
Expected: compilation error — `ProgressBar` not defined.

- [ ] **Step 3: Implement ProgressBar**

```swift
// Claud-o-meter/Sources/ProgressBar.swift
import AppKit

enum UsageLevel {
    case green, orange, red
    
    var nsColor: NSColor {
        switch self {
        case .green:  return NSColor(red: 0.204, green: 0.780, blue: 0.349, alpha: 1) // #34C759
        case .orange: return NSColor(red: 1.000, green: 0.584, blue: 0.000, alpha: 1) // #FF9500
        case .red:    return NSColor(red: 1.000, green: 0.231, blue: 0.188, alpha: 1) // #FF3B30
        }
    }
    
    var sfSymbolName: String {
        switch self {
        case .green:  return "gauge.with.dots.needle.33percent"
        case .orange: return "gauge.with.dots.needle.67percent"
        case .red:    return "bolt.trianglebadge.exclamationmark"
        }
    }
}

enum ProgressBar {
    static let filledChar: Character = "█"
    static let emptyChar: Character = "░"
    
    /// Render a Unicode progress bar string.
    /// - Parameters:
    ///   - percent: 0-100 (clamped)
    ///   - width: number of characters in the bar (default 20)
    static func render(percent: Double, width: Int = 20) -> String {
        let clamped = max(0, min(100, percent))
        let filled = Int((clamped / 100.0) * Double(width))
        let empty = width - filled
        return String(repeating: filledChar, count: filled) + String(repeating: emptyChar, count: empty)
    }
    
    /// Returns the usage level for a given percentage.
    static func color(for percent: Double) -> UsageLevel {
        if percent >= 85 { return .red }
        if percent >= 60 { return .orange }
        return .green
    }
    
    /// Render an attributed string with the bar + percentage, colored appropriately.
    static func attributedBar(percent: Double, width: Int = 20) -> NSAttributedString {
        let bar = render(percent: percent, width: width)
        let level = color(for: percent)
        let pctStr = String(format: "%.0f%%", percent)
        
        let fullStr = "\(bar)  \(pctStr)"
        let attrs: [NSAttributedString.Key: Any] = [
            .font: NSFont(name: "Menlo", size: 12) ?? NSFont.monospacedSystemFont(ofSize: 12, weight: .regular),
            .foregroundColor: level.nsColor
        ]
        return NSAttributedString(string: fullStr, attributes: attrs)
    }
}
```

- [ ] **Step 4: Run tests to verify they pass**

```bash
cd Claud-o-meter && swift test --filter ProgressBarTests 2>&1
```
Expected: all 9 tests PASS.

- [ ] **Step 5: Commit**

```bash
git add Claud-o-meter/Sources/ProgressBar.swift Claud-o-meter/Tests/ProgressBarTests.swift
git commit -m "feat: add ProgressBar utility with color levels"
```

---

### Task 4: Utility — Time Formatter

**Files:**
- Create: `Claud-o-meter/Sources/TimeFormatter.swift`
- Create: `Claud-o-meter/Tests/TimeFormatterTests.swift`

- [ ] **Step 1: Write failing tests**

```swift
// Claud-o-meter/Tests/TimeFormatterTests.swift
import XCTest
@testable import Claud_o_meter

final class TimeFormatterTests: XCTestCase {
    
    func testMinutesOnly() {
        let future = Date().addingTimeInterval(45 * 60) // 45 min
        let result = ResetTimeFormatter.format(future)
        XCTAssertTrue(result.hasPrefix("in "), "Expected 'in Xm', got '\(result)'")
        XCTAssertTrue(result.hasSuffix("m"), "Expected 'in Xm', got '\(result)'")
    }
    
    func testHoursAndMinutes() {
        let future = Date().addingTimeInterval(3 * 3600 + 14 * 60) // 3h 14m
        let result = ResetTimeFormatter.format(future)
        XCTAssertTrue(result.contains("h"), "Expected hours in '\(result)'")
        XCTAssertTrue(result.contains("m"), "Expected minutes in '\(result)'")
        XCTAssertTrue(result.hasPrefix("in "))
    }
    
    func testPastDateReturnsNow() {
        let past = Date().addingTimeInterval(-60)
        let result = ResetTimeFormatter.format(past)
        XCTAssertEqual(result, "now")
    }
    
    func testMoreThan24HoursShowsDayTime() {
        let future = Date().addingTimeInterval(36 * 3600) // 36 hours
        let result = ResetTimeFormatter.format(future)
        // Should be like "mon 3:00pm" — lowercase, contains a colon
        XCTAssertFalse(result.hasPrefix("in "), "More than 24h should show day+time, got '\(result)'")
        XCTAssertTrue(result.contains(":"), "Expected time with colon, got '\(result)'")
    }
    
    func testNilDateReturnsEmptyString() {
        let result = ResetTimeFormatter.format(nil)
        XCTAssertEqual(result, "")
    }
}
```

- [ ] **Step 2: Run tests to verify they fail**

```bash
cd Claud-o-meter && swift test --filter TimeFormatterTests 2>&1
```
Expected: compilation error — `ResetTimeFormatter` not defined.

- [ ] **Step 3: Implement TimeFormatter**

```swift
// Claud-o-meter/Sources/TimeFormatter.swift
import Foundation

enum ResetTimeFormatter {
    /// Format a reset date as a human-friendly string.
    /// - <60 min: "in 45m"
    /// - <24 hours: "in 3h 14m"
    /// - ≥24 hours: "mon 3:00pm" (lowercase day + time)
    /// - past: "now"
    /// - nil: ""
    static func format(_ date: Date?) -> String {
        guard let date = date else { return "" }
        
        let now = Date()
        let totalSeconds = date.timeIntervalSince(now)
        
        if totalSeconds < 0 {
            return "now"
        }
        
        let totalMinutes = Int(totalSeconds / 60)
        
        if totalMinutes < 60 {
            return "in \(totalMinutes)m"
        }
        
        if totalMinutes < 24 * 60 {
            let hours = totalMinutes / 60
            let minutes = totalMinutes % 60
            return "in \(hours)h \(minutes)m"
        }
        
        // More than 24 hours — show day and time
        let formatter = DateFormatter()
        formatter.dateFormat = "EEE h:mma"
        return formatter.string(from: date).lowercased()
    }
}
```

- [ ] **Step 4: Run tests to verify they pass**

```bash
cd Claud-o-meter && swift test --filter TimeFormatterTests 2>&1
```
Expected: all 5 tests PASS.

- [ ] **Step 5: Commit**

```bash
git add Claud-o-meter/Sources/TimeFormatter.swift Claud-o-meter/Tests/TimeFormatterTests.swift
git commit -m "feat: add ResetTimeFormatter utility"
```

---

### Task 5: Login Window + AuthManager

**Files:**
- Create: `Claud-o-meter/Sources/LoginWindow.swift`
- Create: `Claud-o-meter/Sources/AuthManager.swift`

- [ ] **Step 1: Create LoginWindow**

```swift
// Claud-o-meter/Sources/LoginWindow.swift
import AppKit
import WebKit

/// A window that hosts a WKWebView for Claude.ai login.
class LoginWindow: NSWindow {
    let webView: WKWebView
    
    init(webView: WKWebView) {
        self.webView = webView
        
        let frame = NSRect(x: 0, y: 0, width: 480, height: 700)
        super.init(
            contentRect: frame,
            styleMask: [.titled, .closable, .resizable, .miniaturizable],
            backing: .buffered,
            defer: false
        )
        
        self.title = "Claud-o-meter — Sign in to Claude"
        self.contentView = webView
        self.center()
        self.isReleasedWhenClosed = false
        self.minSize = NSSize(width: 400, height: 500)
    }
}
```

- [ ] **Step 2: Create AuthManager**

```swift
// Claud-o-meter/Sources/AuthManager.swift
import AppKit
import WebKit

/// Manages authentication via a persistent WKWebView.
/// The webview's data store retains cookies across app restarts.
@MainActor
class AuthManager: NSObject, WKNavigationDelegate {
    
    private let dataStore: WKWebsiteDataStore
    private let webView: WKWebView
    private var loginWindow: LoginWindow?
    
    /// Called when auth succeeds with the org ID.
    var onAuthSuccess: ((String) -> Void)?
    /// Called when the user needs to re-authenticate.
    var onAuthRequired: (() -> Void)?
    
    /// The org ID, persisted in UserDefaults.
    var orgId: String? {
        get { UserDefaults.standard.string(forKey: "claudeOrgId") }
        set { UserDefaults.standard.set(newValue, forKey: "claudeOrgId") }
    }
    
    override init() {
        // Use a persistent (non-ephemeral) data store — cookies survive restarts.
        // Using default store since WKWebsiteDataStore doesn't have named stores
        // in older macOS. The data persists per-app via the bundle ID.
        self.dataStore = WKWebsiteDataStore.default()
        
        let config = WKWebViewConfiguration()
        config.websiteDataStore = dataStore
        
        self.webView = WKWebView(frame: .zero, configuration: config)
        
        super.init()
        
        webView.navigationDelegate = self
    }
    
    /// The WKWebView used for JavaScript fetch calls.
    var fetchWebView: WKWebView { webView }
    
    /// Check if we have stored credentials and try to use them.
    func attemptAutoLogin() {
        if orgId != nil {
            // We have a stored org ID. Try fetching to see if cookies are still valid.
            // The caller (UsageFetcher) will detect 401 and call showLogin().
            onAuthSuccess?(orgId!)
        } else {
            showLogin()
        }
    }
    
    /// Show the login window.
    func showLogin() {
        let window = LoginWindow(webView: webView)
        window.makeKeyAndOrderFront(nil)
        NSApp.activate(ignoringOtherApps: true)
        self.loginWindow = window
        
        let url = URL(string: "https://claude.ai/login")!
        webView.load(URLRequest(url: url))
    }
    
    /// Attempt a silent session refresh by reloading claude.ai.
    func attemptSilentRefresh(completion: @escaping (Bool) -> Void) {
        let url = URL(string: "https://claude.ai/")!
        webView.load(URLRequest(url: url))
        
        // Wait for navigation to complete, then check cookies
        DispatchQueue.main.asyncAfter(deadline: .now() + 5) { [weak self] in
            self?.extractOrgId { orgId in
                completion(orgId != nil)
            }
        }
    }
    
    /// Log out: clear cookies and stored org ID.
    func logout() {
        orgId = nil
        let types = WKWebsiteDataStore.allWebsiteDataTypes()
        dataStore.removeData(ofTypes: types, modifiedSince: .distantPast) { [weak self] in
            self?.onAuthRequired?()
        }
    }
    
    // MARK: - WKNavigationDelegate
    
    func webView(_ webView: WKWebView, didFinish navigation: WKNavigation!) {
        guard let url = webView.url else { return }
        
        // Auth complete when URL leaves the /login path
        let path = url.path
        if !path.hasPrefix("/login") && !path.hasPrefix("/logout") && path != "/" {
            extractOrgId { [weak self] orgId in
                guard let self = self, let orgId = orgId else { return }
                self.orgId = orgId
                self.loginWindow?.close()
                self.loginWindow = nil
                self.onAuthSuccess?(orgId)
            }
        }
    }
    
    // MARK: - Private
    
    private func extractOrgId(completion: @escaping (String?) -> Void) {
        dataStore.httpCookieStore.getAllCookies { cookies in
            // Try lastActiveOrg cookie first
            if let orgCookie = cookies.first(where: { $0.name == "lastActiveOrg" }) {
                completion(orgCookie.value)
                return
            }
            
            // Fallback: fetch session endpoint via JS
            self.webView.evaluateJavaScript("""
                fetch('/api/auth/session', { credentials: 'include' })
                    .then(r => r.json())
                    .then(d => JSON.stringify(d))
                    .catch(e => JSON.stringify({error: e.message}))
            """) { result, error in
                guard let jsonString = result as? String,
                      let data = jsonString.data(using: .utf8),
                      let json = try? JSONSerialization.jsonObject(with: data) as? [String: Any],
                      let orgId = json["organization_id"] as? String ?? 
                                  (json["account"] as? [String: Any])?["memberships"] as? [[String: Any]] else {
                    completion(nil)
                    return
                }
                // Try to extract from account.memberships[0].organization.uuid
                if let memberships = (json["account"] as? [String: Any])?["memberships"] as? [[String: Any]],
                   let first = memberships.first,
                   let org = first["organization"] as? [String: Any],
                   let uuid = org["uuid"] as? String {
                    completion(uuid)
                    return
                }
                completion(nil)
            }
        }
    }
}
```

- [ ] **Step 3: Build and verify**

```bash
cd Claud-o-meter && swift build 2>&1
```
Expected: `Build complete!`

- [ ] **Step 4: Commit**

```bash
git add Claud-o-meter/Sources/LoginWindow.swift Claud-o-meter/Sources/AuthManager.swift
git commit -m "feat: add AuthManager with WKWebView login and cookie extraction"
```

---

### Task 6: UsageFetcher — JS Fetch via WKWebView

**Files:**
- Create: `Claud-o-meter/Sources/UsageFetcher.swift`

- [ ] **Step 1: Implement UsageFetcher**

```swift
// Claud-o-meter/Sources/UsageFetcher.swift
import Foundation
import WebKit

/// Fetches Claude usage data by evaluating JavaScript fetch() inside a WKWebView.
/// This uses WebKit's TLS stack, which passes Cloudflare's browser fingerprinting.
@MainActor
class UsageFetcher {
    
    private weak var authManager: AuthManager?
    private var timer: Timer?
    private var retryCount = 0
    private let maxRetries = 3
    
    /// Called with fresh usage data on each successful fetch.
    var onUsageUpdate: ((UsageData) -> Void)?
    /// Called when auth has expired.
    var onAuthExpired: (() -> Void)?
    /// Called on network/parse errors.
    var onError: ((String) -> Void)?
    
    init(authManager: AuthManager) {
        self.authManager = authManager
    }
    
    /// Start polling every 5 minutes.
    func startPolling(orgId: String) {
        stopPolling()
        retryCount = 0
        
        // Fetch immediately
        fetch(orgId: orgId)
        
        // Then every 5 minutes
        timer = Timer.scheduledTimer(withTimeInterval: 300, repeats: true) { [weak self] _ in
            Task { @MainActor in
                self?.fetch(orgId: orgId)
            }
        }
    }
    
    /// Stop polling.
    func stopPolling() {
        timer?.invalidate()
        timer = nil
    }
    
    /// Perform a single fetch.
    func fetch(orgId: String) {
        guard let webView = authManager?.fetchWebView else {
            onError?("No webview available")
            return
        }
        
        let js = """
        (async () => {
            try {
                const resp = await fetch('https://claude.ai/api/organizations/\(orgId)/usage', {
                    credentials: 'include',
                    headers: {
                        'accept': '*/*',
                        'anthropic-client-platform': 'web_claude_ai',
                        'content-type': 'application/json'
                    }
                });
                if (resp.status === 401 || resp.status === 403) {
                    return JSON.stringify({ __auth_error: true, status: resp.status });
                }
                const text = await resp.text();
                return text;
            } catch(e) {
                return JSON.stringify({ __network_error: true, message: e.message });
            }
        })()
        """
        
        webView.evaluateJavaScript(js) { [weak self] result, error in
            guard let self = self else { return }
            
            if let error = error {
                self.handleError("JS error: \(error.localizedDescription)")
                return
            }
            
            guard let jsonString = result as? String else {
                self.handleError("Unexpected response type")
                return
            }
            
            guard let data = jsonString.data(using: .utf8) else {
                self.handleError("Could not encode response")
                return
            }
            
            // Check for auth error
            if let json = try? JSONSerialization.jsonObject(with: data) as? [String: Any] {
                if json["__auth_error"] as? Bool == true {
                    self.onAuthExpired?()
                    return
                }
                if json["__network_error"] as? Bool == true {
                    self.handleError(json["message"] as? String ?? "Network error")
                    return
                }
            }
            
            // Parse usage data
            do {
                let usage = try JSONDecoder.apiDecoder.decode(UsageData.self, from: data)
                self.retryCount = 0
                self.onUsageUpdate?(usage)
            } catch {
                self.handleError("Parse error: \(error.localizedDescription)")
            }
        }
    }
    
    private func handleError(_ message: String) {
        retryCount += 1
        if retryCount <= maxRetries {
            // Exponential backoff: 30s, 60s, 120s
            let delay = 30.0 * pow(2.0, Double(retryCount - 1))
            DispatchQueue.main.asyncAfter(deadline: .now() + delay) { [weak self] in
                guard let self = self, let orgId = self.authManager?.orgId else { return }
                self.fetch(orgId: orgId)
            }
        } else {
            onError?(message)
        }
    }
}
```

- [ ] **Step 2: Build and verify**

```bash
cd Claud-o-meter && swift build 2>&1
```
Expected: `Build complete!`

- [ ] **Step 3: Commit**

```bash
git add Claud-o-meter/Sources/UsageFetcher.swift
git commit -m "feat: add UsageFetcher with JS fetch via WKWebView"
```

---

### Task 7: NotificationManager

**Files:**
- Create: `Claud-o-meter/Sources/NotificationManager.swift`

- [ ] **Step 1: Implement NotificationManager**

```swift
// Claud-o-meter/Sources/NotificationManager.swift
import UserNotifications

/// Manages threshold-based notifications for usage alerts.
@MainActor
class NotificationManager: NSObject, UNUserNotificationCenterDelegate {
    
    private let center = UNUserNotificationCenter.current()
    private var permissionGranted = false
    private var lastSessionAlert: AlertLevel = .none
    private var lastWeeklyAlert: AlertLevel = .none
    
    enum AlertLevel: Int, Comparable {
        case none = 0, warning = 1, critical = 2
        
        static func < (lhs: AlertLevel, rhs: AlertLevel) -> Bool {
            lhs.rawValue < rhs.rawValue
        }
    }
    
    override init() {
        super.init()
        center.delegate = self
    }
    
    /// Request notification permission (call once after first successful fetch).
    func requestPermission() {
        center.requestAuthorization(options: [.alert, .sound]) { granted, _ in
            Task { @MainActor in
                self.permissionGranted = granted
            }
        }
    }
    
    /// Check usage data and fire notifications if thresholds crossed.
    func check(_ usage: UsageData) {
        if !permissionGranted {
            requestPermission()
            return
        }
        
        // Session alerts
        if let session = usage.session?.utilization {
            let level = alertLevel(for: session)
            if level > lastSessionAlert {
                let title = level == .critical ? "⚠️ Session at \(Int(session))%" : "Session at \(Int(session))%"
                let body = level == .critical
                    ? "Approaching session limit — slow down or wait for reset."
                    : "Session usage is climbing."
                fire(id: "session-\(level)", title: title, body: body)
            }
            // Reset tracking when usage drops
            if level < lastSessionAlert {
                lastSessionAlert = level
            } else {
                lastSessionAlert = level
            }
        }
        
        // Weekly alert (only critical)
        if let weekly = usage.weeklyAll?.utilization {
            let level = alertLevel(for: weekly)
            if level == .critical && lastWeeklyAlert < .critical {
                let resetStr = ResetTimeFormatter.format(usage.weeklyAll?.resetsAt)
                fire(
                    id: "weekly-critical",
                    title: "⚠️ Weekly at \(Int(weekly))%",
                    body: "Weekly limit approaching. Resets \(resetStr)."
                )
            }
            lastWeeklyAlert = level
        }
    }
    
    private func alertLevel(for percent: Double) -> AlertLevel {
        if percent >= 85 { return .critical }
        if percent >= 60 { return .warning }
        return .none
    }
    
    private func fire(id: String, title: String, body: String) {
        let content = UNMutableNotificationContent()
        content.title = title
        content.body = body
        content.sound = .default
        
        let request = UNNotificationRequest(identifier: id, content: content, trigger: nil)
        center.add(request)
    }
    
    // Show notifications even when app is in foreground
    nonisolated func userNotificationCenter(
        _ center: UNUserNotificationCenter,
        willPresent notification: UNNotification,
        withCompletionHandler completionHandler: @escaping (UNNotificationPresentationOptions) -> Void
    ) {
        completionHandler([.banner, .sound])
    }
}
```

- [ ] **Step 2: Build and verify**

```bash
cd Claud-o-meter && swift build 2>&1
```
Expected: `Build complete!`

- [ ] **Step 3: Commit**

```bash
git add Claud-o-meter/Sources/NotificationManager.swift
git commit -m "feat: add NotificationManager with threshold alerts"
```

---

### Task 8: LaunchAtLoginManager

**Files:**
- Create: `Claud-o-meter/Sources/LaunchAtLoginManager.swift`

- [ ] **Step 1: Implement LaunchAtLoginManager**

```swift
// Claud-o-meter/Sources/LaunchAtLoginManager.swift
import ServiceManagement

/// Manages the "Launch at Login" toggle using SMAppService (macOS 13+).
@MainActor
class LaunchAtLoginManager {
    
    private let service = SMAppService.mainApp
    
    /// Whether launch-at-login is currently enabled.
    var isEnabled: Bool {
        service.status == .enabled
    }
    
    /// Enable launch at login.
    func enable() {
        do {
            try service.register()
        } catch {
            print("Failed to enable launch at login: \(error)")
        }
    }
    
    /// Disable launch at login.
    func disable() {
        do {
            try service.unregister()
        } catch {
            print("Failed to disable launch at login: \(error)")
        }
    }
    
    /// Toggle the current state.
    func toggle() {
        if isEnabled {
            disable()
        } else {
            enable()
        }
    }
}
```

- [ ] **Step 2: Build and verify**

```bash
cd Claud-o-meter && swift build 2>&1
```
Expected: `Build complete!`

- [ ] **Step 3: Commit**

```bash
git add Claud-o-meter/Sources/LaunchAtLoginManager.swift
git commit -m "feat: add LaunchAtLoginManager with SMAppService"
```

---

### Task 9: MenuBarController — Full UI

**Files:**
- Create: `Claud-o-meter/Sources/MenuBarController.swift`

- [ ] **Step 1: Implement MenuBarController**

```swift
// Claud-o-meter/Sources/MenuBarController.swift
import AppKit

/// Manages the NSStatusItem, its icon/title, and the dropdown menu.
@MainActor
class MenuBarController {
    
    private let statusItem: NSStatusItem
    private let launchAtLogin: LaunchAtLoginManager
    
    /// Called when user clicks "Refresh Now".
    var onRefresh: (() -> Void)?
    /// Called when user clicks "Log Out".
    var onLogout: (() -> Void)?
    
    private var currentUsage: UsageData?
    
    init(launchAtLogin: LaunchAtLoginManager) {
        self.statusItem = NSStatusBar.system.statusItem(withLength: NSStatusItem.variableLength)
        self.launchAtLogin = launchAtLogin
        showLoading()
    }
    
    // MARK: - State Updates
    
    /// Show initial loading state.
    func showLoading() {
        guard let button = statusItem.button else { return }
        button.image = NSImage(systemSymbolName: "gauge.with.dots.needle.33percent", accessibilityDescription: "Claude Usage")
        button.title = " --% · --%w"
        buildMenu(usage: nil, state: .loading)
    }
    
    /// Show login required state.
    func showLoginRequired() {
        guard let button = statusItem.button else { return }
        button.image = NSImage(systemSymbolName: "exclamationmark.triangle", accessibilityDescription: "Login required")
        button.title = " Log in"
        button.contentTintColor = .systemOrange
        buildMenu(usage: nil, state: .loginRequired)
    }
    
    /// Show error state.
    func showError(_ message: String) {
        guard let button = statusItem.button else { return }
        button.image = NSImage(systemSymbolName: "exclamationmark.circle", accessibilityDescription: "Error")
        button.title = " Error"
        button.contentTintColor = .systemRed
        buildMenu(usage: nil, state: .error(message))
    }
    
    /// Update with fresh usage data.
    func update(with usage: UsageData) {
        self.currentUsage = usage
        guard let button = statusItem.button else { return }
        
        let sessionPct = usage.session?.utilization ?? 0
        let weeklyPct = usage.weeklyAll?.utilization ?? 0
        let level = ProgressBar.color(for: sessionPct)
        
        // Icon
        button.image = NSImage(systemSymbolName: level.sfSymbolName, accessibilityDescription: "Claude Usage")
        
        // Title: "12% · 8%w"
        let title = " \(Int(sessionPct))% · \(Int(weeklyPct))%w"
        let titleAttr = NSAttributedString(string: title, attributes: [
            .foregroundColor: level.nsColor,
            .font: NSFont.monospacedDigitSystemFont(ofSize: 12, weight: .medium)
        ])
        button.attributedTitle = titleAttr
        button.contentTintColor = level.nsColor
        
        buildMenu(usage: usage, state: .normal)
    }
    
    // MARK: - Menu Building
    
    private enum MenuState {
        case loading, loginRequired, normal, error(String)
    }
    
    private func buildMenu(usage: UsageData?, state: MenuState) {
        let menu = NSMenu()
        
        // Header
        let version = Bundle.main.infoDictionary?["CFBundleShortVersionString"] as? String ?? "1.0.0"
        let header = NSMenuItem(title: "Claud-o-meter v\(version)", action: nil, keyEquivalent: "")
        header.isEnabled = false
        menu.addItem(header)
        menu.addItem(NSMenuItem.separator())
        
        switch state {
        case .loading:
            let item = NSMenuItem(title: "Connecting…", action: nil, keyEquivalent: "")
            item.isEnabled = false
            menu.addItem(item)
            
        case .loginRequired:
            let item = NSMenuItem(title: "Sign in to Claude to start", action: nil, keyEquivalent: "")
            item.isEnabled = false
            menu.addItem(item)
            
        case .error(let message):
            let item = NSMenuItem(title: "Error: \(message)", action: nil, keyEquivalent: "")
            item.isEnabled = false
            menu.addItem(item)
            
        case .normal:
            if let usage = usage {
                addUsageSection(to: menu, label: "SESSION (5hr window)", metric: usage.session)
                addUsageSection(to: menu, label: "WEEKLY · ALL MODELS", metric: usage.weeklyAll)
                addUsageSection(to: menu, label: "WEEKLY · SONNET", metric: usage.weeklySonnet)
                
                if let opus = usage.weeklyOpus {
                    addUsageSection(to: menu, label: "WEEKLY · OPUS", metric: opus)
                }
                
                if let extra = usage.extraUsage, extra.isEnabled {
                    menu.addItem(NSMenuItem.separator())
                    let extraLabel = NSMenuItem(title: "EXTRA USAGE", action: nil, keyEquivalent: "")
                    extraLabel.isEnabled = false
                    menu.addItem(extraLabel)
                    if let util = extra.utilization {
                        let barItem = NSMenuItem()
                        barItem.attributedTitle = ProgressBar.attributedBar(percent: util)
                        barItem.isEnabled = false
                        menu.addItem(barItem)
                    } else {
                        let enabledItem = NSMenuItem(title: "  Enabled (no usage yet)", action: nil, keyEquivalent: "")
                        enabledItem.isEnabled = false
                        menu.addItem(enabledItem)
                    }
                }
            }
        }
        
        menu.addItem(NSMenuItem.separator())
        
        // Actions
        let refreshItem = NSMenuItem(title: "↻ Refresh Now", action: #selector(refreshClicked(_:)), keyEquivalent: "r")
        refreshItem.target = self
        menu.addItem(refreshItem)
        
        let loginItem = NSMenuItem(title: "Launch at Login", action: #selector(toggleLaunchAtLogin(_:)), keyEquivalent: "")
        loginItem.target = self
        loginItem.state = launchAtLogin.isEnabled ? .on : .off
        menu.addItem(loginItem)
        
        menu.addItem(NSMenuItem.separator())
        
        let logoutItem = NSMenuItem(title: "Log Out", action: #selector(logoutClicked(_:)), keyEquivalent: "")
        logoutItem.target = self
        menu.addItem(logoutItem)
        
        menu.addItem(NSMenuItem(title: "Quit Claud-o-meter", action: #selector(NSApplication.terminate(_:)), keyEquivalent: "q"))
        
        statusItem.menu = menu
    }
    
    private func addUsageSection(to menu: NSMenu, label: String, metric: Metric?) {
        guard let metric = metric else { return }
        
        menu.addItem(NSMenuItem.separator())
        
        let labelItem = NSMenuItem(title: label, action: nil, keyEquivalent: "")
        labelItem.isEnabled = false
        // Grey color for section labels
        labelItem.attributedTitle = NSAttributedString(string: label, attributes: [
            .foregroundColor: NSColor.secondaryLabelColor,
            .font: NSFont.systemFont(ofSize: 11, weight: .medium)
        ])
        menu.addItem(labelItem)
        
        let barItem = NSMenuItem()
        barItem.attributedTitle = ProgressBar.attributedBar(percent: metric.utilization)
        barItem.isEnabled = false
        menu.addItem(barItem)
        
        let resetStr = ResetTimeFormatter.format(metric.resetsAt)
        if !resetStr.isEmpty {
            let resetItem = NSMenuItem(title: "  Resets \(resetStr)", action: nil, keyEquivalent: "")
            resetItem.isEnabled = false
            resetItem.attributedTitle = NSAttributedString(string: "  Resets \(resetStr)", attributes: [
                .font: NSFont.systemFont(ofSize: 12)
            ])
            menu.addItem(resetItem)
        }
    }
    
    // MARK: - Actions
    
    @objc private func refreshClicked(_ sender: Any) {
        onRefresh?()
    }
    
    @objc private func toggleLaunchAtLogin(_ sender: NSMenuItem) {
        launchAtLogin.toggle()
        sender.state = launchAtLogin.isEnabled ? .on : .off
    }
    
    @objc private func logoutClicked(_ sender: Any) {
        onLogout?()
    }
}
```

- [ ] **Step 2: Build and verify**

```bash
cd Claud-o-meter && swift build 2>&1
```
Expected: `Build complete!`

- [ ] **Step 3: Commit**

```bash
git add Claud-o-meter/Sources/MenuBarController.swift
git commit -m "feat: add MenuBarController with full dropdown UI"
```

---

### Task 10: Wire Everything Together in AppDelegate

**Files:**
- Modify: `Claud-o-meter/Sources/ClaudOMeterApp.swift`

- [ ] **Step 1: Replace ClaudOMeterApp with full wiring**

Replace the entire contents of `ClaudOMeterApp.swift` with:

```swift
// Claud-o-meter/Sources/ClaudOMeterApp.swift
import AppKit

@main
struct ClaudOMeterApp {
    static func main() {
        let app = NSApplication.shared
        app.setActivationPolicy(.accessory) // no dock icon
        
        let delegate = AppDelegate()
        app.delegate = delegate
        app.run()
    }
}

@MainActor
class AppDelegate: NSObject, NSApplicationDelegate {
    
    private var authManager: AuthManager!
    private var menuBarController: MenuBarController!
    private var usageFetcher: UsageFetcher!
    private var notificationManager: NotificationManager!
    private var launchAtLoginManager: LaunchAtLoginManager!
    
    func applicationDidFinishLaunching(_ notification: Notification) {
        // Initialize components
        authManager = AuthManager()
        launchAtLoginManager = LaunchAtLoginManager()
        menuBarController = MenuBarController(launchAtLogin: launchAtLoginManager)
        usageFetcher = UsageFetcher(authManager: authManager)
        notificationManager = NotificationManager()
        
        // Wire auth callbacks
        authManager.onAuthSuccess = { [weak self] orgId in
            guard let self = self else { return }
            self.menuBarController.showLoading()
            self.usageFetcher.startPolling(orgId: orgId)
            
            // Enable launch at login on first successful auth
            if !self.launchAtLoginManager.isEnabled {
                self.launchAtLoginManager.enable()
            }
        }
        
        authManager.onAuthRequired = { [weak self] in
            self?.usageFetcher.stopPolling()
            self?.menuBarController.showLoginRequired()
            self?.authManager.showLogin()
        }
        
        // Wire fetcher callbacks
        usageFetcher.onUsageUpdate = { [weak self] usage in
            self?.menuBarController.update(with: usage)
            self?.notificationManager.check(usage)
        }
        
        usageFetcher.onAuthExpired = { [weak self] in
            guard let self = self else { return }
            self.usageFetcher.stopPolling()
            // Try silent refresh first
            self.authManager.attemptSilentRefresh { success in
                if success {
                    if let orgId = self.authManager.orgId {
                        self.usageFetcher.startPolling(orgId: orgId)
                    }
                } else {
                    self.menuBarController.showLoginRequired()
                    self.authManager.showLogin()
                }
            }
        }
        
        usageFetcher.onError = { [weak self] message in
            self?.menuBarController.showError(message)
        }
        
        // Wire menu bar actions
        menuBarController.onRefresh = { [weak self] in
            guard let orgId = self?.authManager.orgId else { return }
            self?.usageFetcher.fetch(orgId: orgId)
        }
        
        menuBarController.onLogout = { [weak self] in
            self?.usageFetcher.stopPolling()
            self?.authManager.logout()
        }
        
        // Start
        authManager.attemptAutoLogin()
    }
}
```

- [ ] **Step 2: Build and verify**

```bash
cd Claud-o-meter && swift build 2>&1
```
Expected: `Build complete!`

- [ ] **Step 3: Run and test the full flow**

```bash
cd Claud-o-meter && swift run 2>&1 &
```

Test manually:
1. Menu bar shows gauge icon with `--% · --%w`
2. Login window opens automatically
3. Log into Claude
4. Window closes, menu bar updates with live percentages
5. Click menu bar → dropdown with progress bars, reset timers
6. Click "↻ Refresh Now" → data refreshes
7. Click "Quit Claud-o-meter" → app exits

Kill the process after testing:
```bash
kill %1
```

- [ ] **Step 4: Commit**

```bash
git add Claud-o-meter/Sources/ClaudOMeterApp.swift
git commit -m "feat: wire all components together in AppDelegate"
```

---

### Task 11: Build .app Bundle + DMG Script

**Files:**
- Create: `Claud-o-meter/DMG/create-dmg.sh`
- Create: `Claud-o-meter/Makefile`

- [ ] **Step 1: Create Makefile for building a proper .app bundle**

```makefile
# Claud-o-meter/Makefile
.PHONY: build app dmg clean run

BINARY_NAME = Claud-o-meter
APP_NAME = Claud-o-meter.app
BUILD_DIR = .build/release
APP_DIR = build/$(APP_NAME)

build:
	swift build -c release

app: build
	@echo "Creating $(APP_NAME)..."
	@mkdir -p "$(APP_DIR)/Contents/MacOS"
	@mkdir -p "$(APP_DIR)/Contents/Resources"
	@cp "$(BUILD_DIR)/$(BINARY_NAME)" "$(APP_DIR)/Contents/MacOS/$(BINARY_NAME)"
	@cp Resources/Info.plist "$(APP_DIR)/Contents/Info.plist"
	@echo "APPLClau" > "$(APP_DIR)/Contents/PkgInfo"
	@echo "✅ Built $(APP_DIR)"

dmg: app
	@bash DMG/create-dmg.sh

run: build
	"$(BUILD_DIR)/$(BINARY_NAME)"

clean:
	swift package clean
	rm -rf build/
```

- [ ] **Step 2: Create DMG build script**

```bash
#!/bin/bash
# Claud-o-meter/DMG/create-dmg.sh
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

# Copy app
cp -R "$APP_PATH" "$DMG_DIR/"

# Create symlink to Applications
ln -s /Applications "$DMG_DIR/Applications"

# Create DMG
hdiutil create -volname "$APP_NAME" \
    -srcfolder "$DMG_DIR" \
    -ov -format UDZO \
    "build/$DMG_NAME"

rm -rf "$DMG_DIR"
echo "✅ Created build/$DMG_NAME"
```

- [ ] **Step 3: Make script executable**

```bash
chmod +x Claud-o-meter/DMG/create-dmg.sh
```

- [ ] **Step 4: Build app and DMG**

```bash
cd Claud-o-meter && make app && make dmg
```
Expected: `✅ Built build/Claud-o-meter.app` then `✅ Created build/Claud-o-meter-v1.0.0.dmg`

- [ ] **Step 5: Test the .app directly**

```bash
open Claud-o-meter/build/Claud-o-meter.app
```
Expected: menu bar icon appears, login window opens.

- [ ] **Step 6: Commit**

```bash
git add Claud-o-meter/Makefile Claud-o-meter/DMG/
git commit -m "feat: add Makefile and DMG build script for distribution"
```

---

### Task 12: README for Users

**Files:**
- Create: `Claud-o-meter/README.md`

- [ ] **Step 1: Write user-facing README**

```markdown
# Claud-o-meter

A macOS menu bar app that shows your Claude.ai usage at a glance — session limits, weekly limits, and reset timers. No browser extensions, no config files.

## Install

1. Download **Claud-o-meter-v1.0.0.dmg** from [Releases](https://github.com/YOUR_ORG/claud-o-meter/releases)
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

## FAQ

**Q: Is this official?**
No. This is a community tool that reads your usage from claude.ai. Anthropic could change their API at any time.

**Q: Is my login safe?**
You're logging into claude.ai directly inside the app (same as using Safari). Your credentials are never sent anywhere else. Session cookies are stored locally in the app's sandboxed data.

**Q: My session expired — what do I do?**
The app will try to refresh automatically. If it can't, a login window will appear. Just sign in again.

**Q: How do I uninstall?**
Drag Claud-o-meter from Applications to Trash. To remove launch-at-login, go to System Settings → General → Login Items.
```

- [ ] **Step 2: Commit**

```bash
git add Claud-o-meter/README.md
git commit -m "docs: add user-facing README for Claud-o-meter"
```

---

### Task 13: Final Integration Test

- [ ] **Step 1: Clean build from scratch**

```bash
cd Claud-o-meter && make clean && make app 2>&1
```
Expected: clean build succeeds.

- [ ] **Step 2: Run all unit tests**

```bash
cd Claud-o-meter && swift test 2>&1
```
Expected: all tests in `UsageDataTests`, `ProgressBarTests`, `TimeFormatterTests` pass.

- [ ] **Step 3: Launch the .app and run full flow**

```bash
open Claud-o-meter/build/Claud-o-meter.app
```

Verify:
1. Gauge icon appears in menu bar with `--% · --%w`
2. Login window opens with Claude login page
3. Log in with real credentials
4. Window closes automatically
5. Menu bar updates: green gauge + `X% · Y%w`
6. Click menu → dropdown shows session/weekly/sonnet with progress bars and reset times
7. "Launch at Login" has checkmark
8. "↻ Refresh Now" triggers immediate update
9. "Log Out" clears session and shows login window
10. "Quit Claud-o-meter" exits cleanly

- [ ] **Step 4: Build DMG for distribution**

```bash
cd Claud-o-meter && make dmg 2>&1
```
Expected: `build/Claud-o-meter-v1.0.0.dmg` created.

- [ ] **Step 5: Test DMG install flow**

```bash
open Claud-o-meter/build/Claud-o-meter-v1.0.0.dmg
```
Verify: DMG mounts, shows app + Applications shortcut, drag-to-install works.

- [ ] **Step 6: Final commit**

```bash
git add -A && git commit -m "feat: Claud-o-meter v1.0.0 — complete native macOS menu bar app"
```
