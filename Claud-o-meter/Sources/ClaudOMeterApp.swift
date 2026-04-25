import AppKit
import Darwin

func cmlog(_ msg: String) { fputs("[CM] \(msg)\n", stderr) }

// Strong reference to prevent ARC deallocation (NSApplication.delegate is weak)
private var _delegate: AppDelegate?

@main
struct ClaudOMeterApp {
    static func main() {
        // Ensure only one instance runs at a time
        let runningApps = NSWorkspace.shared.runningApplications.filter {
            $0.bundleIdentifier == "com.claudometer.app"
        }
        if runningApps.count > 1 {
            // Another instance is already running — quit silently
            exit(0)
        }

        // Also check by process name for dev builds (no bundle ID)
        let myPID = ProcessInfo.processInfo.processIdentifier
        let sameName = NSWorkspace.shared.runningApplications.filter {
            $0.localizedName == "Claud-o-meter" && $0.processIdentifier != myPID
        }
        if !sameName.isEmpty {
            exit(0)
        }

        let app = NSApplication.shared
        app.setActivationPolicy(.accessory)

        // NSApplication.delegate is weak — store delegate in a static var
        // so ARC doesn't deallocate it (and the menu bar icon with it).
        _delegate = AppDelegate()
        app.delegate = _delegate
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
        cmlog("didFinishLaunching START")
        authManager = AuthManager()
        cmlog("authManager created")
        launchAtLoginManager = LaunchAtLoginManager()
        cmlog("launchAtLoginManager created")
        menuBarController = MenuBarController(launchAtLogin: launchAtLoginManager)
        cmlog("menuBarController created")
        usageFetcher = UsageFetcher(authManager: authManager)
        cmlog("usageFetcher created")
        notificationManager = NotificationManager()
        cmlog("notificationManager created")

        authManager.onAuthSuccess = { [weak self] orgId in
            guard let self = self else { return }
            self.menuBarController.showLoading()
            self.usageFetcher.startPolling(orgId: orgId)

            if !self.launchAtLoginManager.isEnabled {
                self.launchAtLoginManager.enable()
            }
        }

        authManager.onAuthRequired = { [weak self] in
            self?.usageFetcher.stopPolling()
            self?.menuBarController.showLoginRequired()
            self?.authManager.showLogin()
        }

        usageFetcher.onUsageUpdate = { [weak self] usage in
            self?.menuBarController.update(with: usage)
            self?.notificationManager.check(usage)
        }

        usageFetcher.onAuthExpired = { [weak self] in
            guard let self = self else { return }
            self.usageFetcher.stopPolling()
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

        menuBarController.onRefresh = { [weak self] in
            guard let orgId = self?.authManager.orgId else { return }
            self?.usageFetcher.fetch(orgId: orgId)
        }

        menuBarController.onLogout = { [weak self] in
            self?.usageFetcher.stopPolling()
            self?.authManager.logout()
        }

        cmlog("all callbacks wired, calling attemptAutoLogin")
        authManager.attemptAutoLogin()
        cmlog("didFinishLaunching END")
    }
}
