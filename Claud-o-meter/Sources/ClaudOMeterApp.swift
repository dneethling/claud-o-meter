import AppKit
import Darwin

func cmlog(_ msg: String) {
    let line = "[CM] \(msg)\n"
    fputs(line, stderr)
    // Also write to file since `open` doesn't capture stderr
    if let data = line.data(using: .utf8) {
        let logFile = "/tmp/claudometer.log"
        if FileManager.default.fileExists(atPath: logFile) {
            if let fh = FileHandle(forWritingAtPath: logFile) {
                fh.seekToEndOfFile()
                fh.write(data)
                fh.closeFile()
            }
        } else {
            FileManager.default.createFile(atPath: logFile, contents: data)
        }
    }
}

private var _delegate: AppDelegate?

@main
struct ClaudOMeterApp {
    static func main() {
        let app = NSApplication.shared
        app.setActivationPolicy(.accessory)

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
        launchAtLoginManager = LaunchAtLoginManager()
        menuBarController = MenuBarController(launchAtLogin: launchAtLoginManager)
        usageFetcher = UsageFetcher(authManager: authManager)
        notificationManager = NotificationManager()
        cmlog("all components created")

        authManager.onAuthSuccess = { [weak self] orgId in
            guard let self = self else { return }
            cmlog("onAuthSuccess: orgId=\(orgId.prefix(8))")
            self.menuBarController.showLoading()
            self.usageFetcher.startPolling(orgId: orgId)

            if !self.launchAtLoginManager.isEnabled {
                self.launchAtLoginManager.enable()
            }
        }

        authManager.onAuthRequired = { [weak self] in
            cmlog("onAuthRequired")
            self?.usageFetcher.stopPolling()
            self?.menuBarController.showLoginRequired()
            self?.authManager.showLogin()
        }

        usageFetcher.onUsageUpdate = { [weak self] usage in
            cmlog("onUsageUpdate: session=\(usage.session?.utilization ?? -1)")
            self?.menuBarController.update(with: usage)
            self?.notificationManager.check(usage)
        }

        usageFetcher.onAuthExpired = { [weak self] in
            guard let self = self else { return }
            cmlog("onAuthExpired")
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
            cmlog("onError: \(message)")
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

        cmlog("calling attemptAutoLogin")
        authManager.attemptAutoLogin()
        cmlog("didFinishLaunching END")
    }
}
