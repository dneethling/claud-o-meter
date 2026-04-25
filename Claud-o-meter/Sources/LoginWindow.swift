import AppKit
import WebKit

class LoginWindow: NSWindow {
    let webView: WKWebView

    init(webView: WKWebView) {
        self.webView = webView

        let frame = NSRect(x: 0, y: 0, width: 480, height: 700)
        super.init(
            contentRect: frame,
            styleMask: [.titled, .closable, .resizable, .miniaturizable],
            backing: .buffered,
            defer: false
        )

        self.title = "Claud-o-meter — Sign in to Claude"
        self.contentView = webView
        self.center()
        self.isReleasedWhenClosed = false
        self.minSize = NSSize(width: 400, height: 500)
    }
}
