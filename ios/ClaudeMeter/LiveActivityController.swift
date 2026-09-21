import ActivityKit
import Foundation

// Owns the Live Activity lifecycle: request it, stream its APNs push token to the
// relay, and end it. The relay does the actual updating over push — this app only
// needs to run long enough to start the activity and hand off the token.
@MainActor
final class LiveActivityController: ObservableObject {
    @Published var isRunning = false
    @Published var pushToken: String?
    @Published var lastError: String?

    private var tokenTask: Task<Void, Never>?

    var activitiesEnabled: Bool {
        ActivityAuthorizationInfo().areActivitiesEnabled
    }

    /// Reflect any activity already running (e.g. after the app was relaunched).
    func syncExisting(relay: RelayClient?) {
        if let activity = Activity<ClaudeUsageAttributes>.activities.first {
            isRunning = true
            observeToken(activity: activity, relay: relay)
        } else {
            isRunning = false
        }
    }

    func start(relay: RelayClient?) {
        guard activitiesEnabled else {
            lastError = "Live Activities are turned off. Enable them in Settings › Claude Meter."
            return
        }
        // Avoid stacking duplicates.
        if let existing = Activity<ClaudeUsageAttributes>.activities.first {
            isRunning = true
            observeToken(activity: existing, relay: relay)
            return
        }

        let attributes = ClaudeUsageAttributes(title: "Claude usage")
        let content = ActivityContent(state: .placeholder, staleDate: nil)
        do {
            let activity = try Activity.request(
                attributes: attributes,
                content: content,
                pushType: .token)
            isRunning = true
            lastError = nil
            observeToken(activity: activity, relay: relay)
        } catch {
            lastError = error.localizedDescription
        }
    }

    private func observeToken(activity: Activity<ClaudeUsageAttributes>, relay: RelayClient?) {
        tokenTask?.cancel()
        tokenTask = Task { [weak self] in
            for await tokenData in activity.pushTokenUpdates {
                let hex = tokenData.map { String(format: "%02x", $0) }.joined()
                await MainActor.run { self?.pushToken = hex }
                if let relay {
                    let ok = await relay.register(token: hex, activityId: activity.id)
                    if !ok {
                        await MainActor.run {
                            self?.lastError = "Couldn't reach the relay to register the push token."
                        }
                    }
                }
            }
        }
    }

    func stop(relay: RelayClient?) {
        let token = pushToken
        tokenTask?.cancel()
        tokenTask = nil
        Task { [weak self] in
            if let relay, let token { await relay.unregister(token: token) }
            for activity in Activity<ClaudeUsageAttributes>.activities {
                await activity.end(nil, dismissalPolicy: .immediate)
            }
            await MainActor.run {
                self?.isRunning = false
                self?.pushToken = nil
            }
        }
    }
}
