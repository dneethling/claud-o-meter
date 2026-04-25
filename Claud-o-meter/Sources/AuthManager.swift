import AppKit
import WebKit

@MainActor
class AuthManager: NSObject, WKNavigationDelegate, WKUIDelegate {

    private let dataStore: WKWebsiteDataStore
    private let webView: WKWebView
    private var loginWindow: LoginWindow?
    private var popupWebView: WKWebView? // For Google OAuth popup

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
        // Allow JavaScript to open popups (needed for Google OAuth)
        let prefs = WKWebpagePreferences()
        prefs.allowsContentJavaScript = true
        config.defaultWebpagePreferences = prefs

        self.webView = WKWebView(frame: .zero, configuration: config)
        // Set a real Safari user agent so Google doesn't block OAuth
        self.webView.customUserAgent = "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/605.1.15 (KHTML, like Gecko) Version/17.4 Safari/605.1.15"

        super.init()

        webView.navigationDelegate = self
        webView.uiDelegate = self
    }

    var fetchWebView: WKWebView { webView }

    /// Check if we have stored credentials and try to use them.
    /// If we have an org ID, first navigate to claude.ai so cookies are in the JS context,
    /// then signal auth success.
    func attemptAutoLogin() {
        if let orgId = orgId {
            // Load a claude.ai page first so the WKWebView has cookie context for JS fetch()
            let url = URL(string: "https://claude.ai/")!
            webView.load(URLRequest(url: url))

            // Wait for the page to load, then start fetching
            DispatchQueue.main.asyncAfter(deadline: .now() + 3) { [weak self] in
                self?.onAuthSuccess?(orgId)
            }
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

        // If this is the popup webview returning from Google OAuth, check if it landed on claude.ai
        if webView === popupWebView {
            if url.host == "claude.ai" && !path.hasPrefix("/login") {
                // Google OAuth completed, close popup and check main webview
                popupWebView?.removeFromSuperview()
                popupWebView = nil
                // Reload main webview to pick up the authenticated session
                self.webView.load(URLRequest(url: URL(string: "https://claude.ai/")!))
            }
            return
        }

        // Auth complete when URL leaves the /login path and is on claude.ai
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

    // MARK: - WKUIDelegate (handles Google OAuth popup)

    /// Called when JavaScript tries to open a new window (e.g. Google OAuth popup).
    /// We create a new WKWebView sharing the same data store and display it in the login window.
    func webView(
        _ webView: WKWebView,
        createWebViewWith configuration: WKWebViewConfiguration,
        for navigationAction: WKNavigationAction,
        windowFeatures: WKWindowFeatures
    ) -> WKWebView? {
        // Share the same data store so cookies transfer
        configuration.websiteDataStore = dataStore

        let popup = WKWebView(frame: webView.bounds, configuration: configuration)
        popup.customUserAgent = webView.customUserAgent
        popup.navigationDelegate = self
        popup.uiDelegate = self
        popup.autoresizingMask = [.width, .height]

        // Add the popup webview on top of the main webview in the login window
        if let contentView = loginWindow?.contentView {
            contentView.addSubview(popup)
            popup.frame = contentView.bounds
        }

        self.popupWebView = popup
        return popup
    }

    /// Called when the popup wants to close itself.
    func webViewDidClose(_ webView: WKWebView) {
        if webView === popupWebView {
            popupWebView?.removeFromSuperview()
            popupWebView = nil
        }
    }

    // Handle navigation actions that open in new windows (target="_blank" links)
    func webView(
        _ webView: WKWebView,
        decidePolicyFor navigationAction: WKNavigationAction,
        decisionHandler: @escaping (WKNavigationActionPolicy) -> Void
    ) {
        // If it's a link that would normally open in a new window, load it in the same webview
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
