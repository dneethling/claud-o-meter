import AppKit
import WebKit

@MainActor
class AuthManager: NSObject, WKNavigationDelegate, WKUIDelegate {

    private let dataStore: WKWebsiteDataStore
    private let webView: WKWebView
    private var loginWindow: LoginWindow?
    private var popupWebView: WKWebView?

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
        let prefs = WKWebpagePreferences()
        prefs.allowsContentJavaScript = true
        config.defaultWebpagePreferences = prefs

        self.webView = WKWebView(frame: .zero, configuration: config)
        self.webView.customUserAgent = "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/605.1.15 (KHTML, like Gecko) Version/17.4 Safari/605.1.15"

        super.init()

        webView.navigationDelegate = self
        webView.uiDelegate = self
    }

    var fetchWebView: WKWebView { webView }

    // MARK: - Auth Flow

    func attemptAutoLogin() {
        if let orgId = orgId {
            // Load claude.ai so cookies are in JS context for fetch()
            webView.load(URLRequest(url: URL(string: "https://claude.ai/")!))
            DispatchQueue.main.asyncAfter(deadline: .now() + 3) { [weak self] in
                self?.onAuthSuccess?(orgId)
            }
        } else {
            // Try importing cookies from the user's browser first
            importFromBrowser()
        }
    }

    func showLogin() {
        showEmbeddedLogin()
    }

    // MARK: - Browser Cookie Import (primary auth method)

    /// Try to read cookies directly from Arc/Chrome/Brave.
    /// If successful, inject into WKWebView and start fetching.
    /// If not, fall back to the embedded WKWebView login.
    private func importFromBrowser() {
        do {
            let result = try BrowserCookieImporter.importCookies()

            // Inject cookies into WKWebView's cookie store
            let cookieStore = dataStore.httpCookieStore
            let group = DispatchGroup()

            for cookie in result.cookies {
                group.enter()
                cookieStore.setCookie(cookie) { group.leave() }
            }

            group.notify(queue: .main) { [weak self] in
                guard let self = self else { return }

                if let orgId = result.orgId {
                    self.orgId = orgId
                    // Load claude.ai so the WKWebView has the cookies in context
                    self.webView.load(URLRequest(url: URL(string: "https://claude.ai/")!))
                    DispatchQueue.main.asyncAfter(deadline: .now() + 2) {
                        self.onAuthSuccess?(orgId)
                    }
                    print("[Claud-o-meter] Imported \(result.cookies.count) cookies from \(result.browserName)")
                } else {
                    // Got cookies but no org ID — try embedded login
                    self.showEmbeddedLogin()
                }
            }
        } catch {
            print("[Claud-o-meter] Browser import failed: \(error.localizedDescription)")
            // Fall back to embedded WKWebView login
            showEmbeddedLogin()
        }
    }

    /// Show WKWebView login window. Works for email login.
    /// For Google SSO with passkeys, shows guidance.
    private func showEmbeddedLogin() {
        let window = LoginWindow(webView: webView)
        window.makeKeyAndOrderFront(nil)
        NSApp.activate(ignoringOtherApps: true)
        self.loginWindow = window

        webView.load(URLRequest(url: URL(string: "https://claude.ai/login")!))
    }

    func attemptSilentRefresh(completion: @escaping (Bool) -> Void) {
        // Try browser cookie import first (cookies may have been refreshed by the browser)
        do {
            let result = try BrowserCookieImporter.importCookies()
            let cookieStore = dataStore.httpCookieStore
            let group = DispatchGroup()
            for cookie in result.cookies {
                group.enter()
                cookieStore.setCookie(cookie) { group.leave() }
            }
            group.notify(queue: .main) { [weak self] in
                self?.webView.load(URLRequest(url: URL(string: "https://claude.ai/")!))
                DispatchQueue.main.asyncAfter(deadline: .now() + 2) {
                    completion(true)
                }
            }
            return
        } catch {
            // Fall back to webview reload
        }

        webView.load(URLRequest(url: URL(string: "https://claude.ai/")!))
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

        if webView === popupWebView {
            if url.host == "claude.ai" && !path.hasPrefix("/login") {
                popupWebView?.removeFromSuperview()
                popupWebView = nil
                self.webView.load(URLRequest(url: URL(string: "https://claude.ai/")!))
            }
            return
        }

        if url.host == "claude.ai" && !path.hasPrefix("/login") && !path.hasPrefix("/logout") && path != "/" {
            extractOrgId { [weak self] orgId in
                guard let self = self, let orgId = orgId else { return }
                self.orgId = orgId
                self.popupWebView?.removeFromSuperview()
                self.popupWebView = nil
                self.loginWindow?.close()
                self.loginWindow = nil
                self.onAuthSuccess?(orgId)
            }
        }
    }

    // MARK: - WKUIDelegate (Google OAuth popup)

    func webView(
        _ webView: WKWebView,
        createWebViewWith configuration: WKWebViewConfiguration,
        for navigationAction: WKNavigationAction,
        windowFeatures: WKWindowFeatures
    ) -> WKWebView? {
        configuration.websiteDataStore = dataStore

        let popup = WKWebView(frame: webView.bounds, configuration: configuration)
        popup.customUserAgent = webView.customUserAgent
        popup.navigationDelegate = self
        popup.uiDelegate = self
        popup.autoresizingMask = [.width, .height]

        if let contentView = loginWindow?.contentView {
            contentView.addSubview(popup)
            popup.frame = contentView.bounds
        }

        self.popupWebView = popup
        return popup
    }

    func webViewDidClose(_ webView: WKWebView) {
        if webView === popupWebView {
            popupWebView?.removeFromSuperview()
            popupWebView = nil
        }
    }

    func webView(
        _ webView: WKWebView,
        decidePolicyFor navigationAction: WKNavigationAction,
        decisionHandler: @escaping (WKNavigationActionPolicy) -> Void
    ) {
        if navigationAction.targetFrame == nil {
            webView.load(navigationAction.request)
            decisionHandler(.cancel)
            return
        }
        decisionHandler(.allow)
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
