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
        fputs("[CM] fetch: starting for org=\(orgId.prefix(8))..., webView.url=\(authManager?.fetchWebView.url?.absoluteString ?? "nil")\n", stderr)
        guard let webView = authManager?.fetchWebView else {
            fputs("[CM] fetch: NO WEBVIEW\n", stderr)
            onError?("No webview available")
            return
        }

        let js = """
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
        """

        fputs("[CM] fetch: calling callAsyncJavaScript...\n", stderr)
        // callAsyncJavaScript properly handles async/await and Promises (macOS 11+)
        webView.callAsyncJavaScript(js, arguments: [:], in: nil, in: .page) { [weak self] result in
            guard let self = self else { return }

            switch result {
            case .failure(let error):
                fputs("[CM] fetch: JS ERROR: \(error.localizedDescription)\n", stderr)
                self.handleError("JS error: \(error.localizedDescription)")

            case .success(let value):
                guard let jsonString = value as? String,
                      let data = jsonString.data(using: .utf8) else {
                    fputs("[CM] fetch: unexpected result type: \(type(of: value)), value: \(String(describing: value))\n", stderr)
                    self.handleError("Unexpected response type")
                    return
                }

                fputs("[CM] fetch: got response (\(jsonString.count) chars): \(jsonString.prefix(120))...\n", stderr)

                if let json = try? JSONSerialization.jsonObject(with: data) as? [String: Any] {
                    if json["__auth_error"] as? Bool == true {
                        fputs("[CM] fetch: AUTH ERROR\n", stderr)
                        self.onAuthExpired?()
                        return
                    }
                    if json["__network_error"] as? Bool == true {
                        fputs("[CM] fetch: NETWORK ERROR: \(json["message"] ?? "")\n", stderr)
                        self.handleError(json["message"] as? String ?? "Network error")
                        return
                    }
                }

                do {
                    let usage = try JSONDecoder.apiDecoder.decode(UsageData.self, from: data)
                    fputs("[CM] fetch: SUCCESS session=\(usage.session?.utilization ?? -1)%\n", stderr)
                    self.retryCount = 0
                    self.onUsageUpdate?(usage)
                } catch {
                    fputs("[CM] fetch: PARSE ERROR: \(error)\n", stderr)
                    self.handleError("Parse error: \(error.localizedDescription)")
                }
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
