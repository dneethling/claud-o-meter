import UserNotifications

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

    func requestPermission() {
        center.requestAuthorization(options: [.alert, .sound]) { granted, _ in
            Task { @MainActor in
                self.permissionGranted = granted
            }
        }
    }

    func check(_ usage: UsageData) {
        if !permissionGranted {
            requestPermission()
            return
        }

        if let session = usage.session?.utilization {
            let level = alertLevel(for: session)
            if level > lastSessionAlert {
                let title = level == .critical ? "⚠️ Session at \(Int(session))%" : "Session at \(Int(session))%"
                let body = level == .critical
                    ? "Approaching session limit — slow down or wait for reset."
                    : "Session usage is climbing."
                fire(id: "session-\(level)", title: title, body: body)
            }
            lastSessionAlert = level
        }

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

    nonisolated func userNotificationCenter(
        _ center: UNUserNotificationCenter,
        willPresent notification: UNNotification,
        withCompletionHandler completionHandler: @escaping (UNNotificationPresentationOptions) -> Void
    ) {
        completionHandler([.banner, .sound])
    }
}
