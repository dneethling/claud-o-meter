import ServiceManagement

@MainActor
class LaunchAtLoginManager {

    private let service = SMAppService.mainApp

    var isEnabled: Bool {
        service.status == .enabled
    }

    func enable() {
        do {
            try service.register()
        } catch {
            print("Failed to enable launch at login: \(error)")
        }
    }

    func disable() {
        do {
            try service.unregister()
        } catch {
            print("Failed to disable launch at login: \(error)")
        }
    }

    func toggle() {
        if isEnabled {
            disable()
        } else {
            enable()
        }
    }
}
