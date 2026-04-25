import AppKit

@MainActor
class MenuBarController {

    private let statusItem: NSStatusItem
    private let launchAtLogin: LaunchAtLoginManager

    var onRefresh: (() -> Void)?
    var onLogout: (() -> Void)?

    private var currentUsage: UsageData?

    init(launchAtLogin: LaunchAtLoginManager) {
        self.statusItem = NSStatusBar.system.statusItem(withLength: NSStatusItem.variableLength)
        self.launchAtLogin = launchAtLogin
        fputs("[CM] MenuBarController.init: statusItem=\(statusItem), button=\(String(describing: statusItem.button)), isOnMainThread=\(Thread.isMainThread)\n", stderr)
        showLoading()
    }

    func showLoading() {
        guard let button = statusItem.button else {
            fputs("[CM] showLoading: button is NIL!\n", stderr)
            return
        }
        button.image = NSImage(systemSymbolName: "gauge.with.dots.needle.33percent", accessibilityDescription: "Claude Usage")
        button.title = " --% · --%w"
        buildMenu(usage: nil, state: .loading)
    }

    func showLoginRequired() {
        guard let button = statusItem.button else { return }
        button.image = NSImage(systemSymbolName: "exclamationmark.triangle", accessibilityDescription: "Login required")
        button.title = " Log in"
        button.contentTintColor = .systemOrange
        buildMenu(usage: nil, state: .loginRequired)
    }

    func showError(_ message: String) {
        guard let button = statusItem.button else { return }
        button.image = NSImage(systemSymbolName: "exclamationmark.circle", accessibilityDescription: "Error")
        button.title = " Error"
        button.contentTintColor = .systemRed
        buildMenu(usage: nil, state: .error(message))
    }

    func update(with usage: UsageData) {
        self.currentUsage = usage
        guard let button = statusItem.button else {
            cmlog("update: button is NIL")
            return
        }
        cmlog("update: setting button with session=\(usage.session?.utilization ?? -1)")

        let sessionPct = usage.session?.utilization ?? 0
        let weeklyPct = usage.weeklyAll?.utilization ?? 0
        let level = ProgressBar.color(for: sessionPct)

        button.image = NSImage(systemSymbolName: level.sfSymbolName, accessibilityDescription: "Claude Usage")
        button.title = " \(Int(sessionPct))% · \(Int(weeklyPct))%w"
        button.contentTintColor = level.nsColor

        buildMenu(usage: usage, state: .normal)
    }

    private enum MenuState {
        case loading, loginRequired, normal, error(String)
    }

    private func buildMenu(usage: UsageData?, state: MenuState) {
        let menu = NSMenu()

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

    @objc nonisolated private func refreshClicked(_ sender: Any) {
        Task { @MainActor in self.onRefresh?() }
    }

    @objc nonisolated private func toggleLaunchAtLogin(_ sender: NSMenuItem) {
        Task { @MainActor in
            self.launchAtLogin.toggle()
            sender.state = self.launchAtLogin.isEnabled ? .on : .off
        }
    }

    @objc nonisolated private func logoutClicked(_ sender: Any) {
        Task { @MainActor in self.onLogout?() }
    }
}
