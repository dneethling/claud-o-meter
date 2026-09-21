import SwiftUI

// Maps the relay's semantic colour names to concrete colours. These match the
// SwiftBar menu-bar widget's palette (#34C759 / #FF9500 / #FF3B30) so the phone
// and the Mac agree at a glance.
enum MeterColor {
    static func named(_ name: String) -> Color {
        switch name {
        case "red":    return Color(red: 1.00, green: 0.23, blue: 0.19)   // #FF3B30
        case "orange": return Color(red: 1.00, green: 0.58, blue: 0.00)   // #FF9500
        case "green":  return Color(red: 0.20, green: 0.78, blue: 0.35)   // #34C759
        default:       return .secondary
        }
    }
}

extension Optional where Wrapped == Int {
    /// A percentage for display: the number, or an em-dash when unknown.
    var pctLabel: String {
        if let v = self { return "\(v)%" }
        return "—"
    }
    /// Clamped 0…1 fraction for ring/gauge fills (0 when unknown).
    var pctFraction: Double {
        guard let v = self else { return 0 }
        return min(max(Double(v) / 100.0, 0), 1)
    }
}

extension ClaudeUsageAttributes.ContentState {
    var isHealthy: Bool { status == "ok" }
    /// The metric that best represents "how close am I to a wall right now" —
    /// session normally, credit spend once the weekly limit is exhausted.
    var headlineColorName: String { on_credits ? "orange" : session_color }
}
