import Foundation
import WebKit

@MainActor
class UsageFetcher {

    private weak var authManager: AuthManager?
    private var timer: Timer?
    private var retryCount = 0
    private let maxRetries = 3

    var onUsageUpdate: ((UsageData) -> Void)?
    var onAuthExpired: (() -> Void)?
    var onError: ((String) -> Void)?

    init(authManager: AuthManager) {
        self.authManager = authManager
    }

    func startPolling(orgId: String) {
        stopPolling()
        retryCount = 0
        fetch(orgId: orgId)
        timer = Timer.scheduledTimer(withTimeInterval: 300, repeats: true) { [weak self] _ in
            Task { @MainActor in
                self?.fetch(orgId: orgId)
            }
        }
    }

    func stopPolling() {
        timer?.invalidate()
        timer = nil
    }

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

            guard let jsonString = result as? String,
                  let data = jsonString.data(using: .utf8) else {
                self.handleError("Unexpected response type")
                return
            }

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
