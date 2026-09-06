import XCTest
@testable import Codenotch

@MainActor
final class ComparisonEditionTests: XCTestCase {
    func testOtherProvidersAreOptIn() {
        let name = "ComparisonEditionTests.\(UUID().uuidString)"
        let defaults = UserDefaults(suiteName: name)!
        defer { defaults.removePersistentDomain(forName: name) }
        let preferences = Preferences(defaults: defaults)
        XCTAssertTrue(preferences.isConnected("claude"))
        XCTAssertTrue(preferences.isConnected("codex"))
        for id in ["cursor", "gemini", "glm"] {
            XCTAssertFalse(preferences.isConnected(id))
        }
        preferences.setConnected(true, for: "cursor")
        XCTAssertTrue(Preferences(defaults: defaults).isConnected("cursor"))
    }

    func testAppIdentityAndFeedAreIndependent() {
        let bundle = Bundle.main
        XCTAssertEqual(bundle.bundleIdentifier, "com.darren.claudometer.notch")
        XCTAssertEqual(bundle.object(forInfoDictionaryKey: "SUFeedURL") as? String,
                       "https://github.com/dneethling/claud-o-meter/releases/download/notch-updates/appcast.xml")
    }
}
