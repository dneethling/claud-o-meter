// Claud-o-meter/Sources/ProgressBar.swift
import AppKit

enum UsageLevel: Equatable {
    case green, orange, red

    var nsColor: NSColor {
        switch self {
        case .green:  return NSColor(red: 0.204, green: 0.780, blue: 0.349, alpha: 1) // #34C759
        case .orange: return NSColor(red: 1.000, green: 0.584, blue: 0.000, alpha: 1) // #FF9500
        case .red:    return NSColor(red: 1.000, green: 0.231, blue: 0.188, alpha: 1) // #FF3B30
        }
    }

    var sfSymbolName: String {
        switch self {
        case .green:  return "gauge.with.dots.needle.33percent"
        case .orange: return "gauge.with.dots.needle.67percent"
        case .red:    return "bolt.trianglebadge.exclamationmark"
        }
    }
}

enum ProgressBar {
    static let filledChar: Character = "█"
    static let emptyChar: Character = "░"

    static func render(percent: Double, width: Int = 20) -> String {
        let clamped = max(0, min(100, percent))
        let filled = Int((clamped / 100.0) * Double(width))
        let empty = width - filled
        return String(repeating: filledChar, count: filled) + String(repeating: emptyChar, count: empty)
    }

    static func color(for percent: Double) -> UsageLevel {
        if percent >= 85 { return .red }
        if percent >= 60 { return .orange }
        return .green
    }

    static func attributedBar(percent: Double, width: Int = 20) -> NSAttributedString {
        let bar = render(percent: percent, width: width)
        let level = color(for: percent)
        let pctStr = String(format: "%.0f%%", percent)

        let fullStr = "\(bar)  \(pctStr)"
        let attrs: [NSAttributedString.Key: Any] = [
            .font: NSFont(name: "Menlo", size: 12) ?? NSFont.monospacedSystemFont(ofSize: 12, weight: .regular),
            .foregroundColor: level.nsColor
        ]
        return NSAttributedString(string: fullStr, attributes: attrs)
    }
}
