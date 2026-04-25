import AppKit
import WebKit

@MainActor
class AuthManager: NSObject, WKNavigationDelegate {

    private let dataStore: WKWebsiteDataStore
    private let webView: WKWebView
    private var loginWindow: LoginWindow?

    var onAuthSuccess: ((String) -> Void)?
    var onAuthRequired: (() -> Void)?

    var orgId: String? {
        get { UserDefaults.standard.string(forKey: "claudeOrgId") }
        set { UserDefaults.standard.set(newValue, forKey: "claudeOrgId") }
    }

    override init() {
        self.dataStore = WKWebsiteDataStore.default()

        let config = WKWebViewConfiguration()
        config.websiteDataStore = dataStore

        self.webView = WKWebView(frame: .zero, configuration: config)

        super.init()

        webView.navigationDelegate = self
    }

    var fetchWebView: WKWebView { webView }

    func attemptAutoLogin() {
        if orgId != nil {
            onAuthSuccess?(orgId!)
        } else {
            showLogin()
        }
    }

    func showLogin() {
        let window = LoginWindow(webView: webView)
        window.makeKeyAndOrderFront(nil)
        NSApp.activate(ignoringOtherApps: true)
        self.loginWindow = window

        let url = URL(string: "https://claude.ai/login")!
        webView.load(URLRequest(url: url))
    }

    func attemptSilentRefresh(completion: @escaping (Bool) -> Void) {
        let url = URL(string: "https://claude.ai/")!
        webView.load(URLRequest(url: url))

        DispatchQueue.main.asyncAfter(deadline: .now() + 5) { [weak self] in
            self?.extractOrgId { orgId in
                completion(orgId != nil)
            }
        }
    }

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
            if let orgCookie = cookies.first(where: { $0.name == "lastActiveOrg" }) {
                completion(orgCookie.value)
                return
            }

            self.webView.evaluateJavaScript("""
                fetch('/api/auth/session', { credentials: 'include' })
                    .then(r => r.json())
                    .then(d => JSON.stringify(d))
                    .catch(e => JSON.stringify({error: e.message}))
            """) { result, error in
                guard let jsonString = result as? String,
                      let data = jsonString.data(using: .utf8),
                      let json = try? JSONSerialization.jsonObject(with: data) as? [String: Any] else {
                    completion(nil)
                    return
                }
                if let orgId = json["organization_id"] as? String {
                    completion(orgId)
                    return
                }
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
