// Claud-o-meter/Sources/TimeFormatter.swift
import Foundation

enum ResetTimeFormatter {
    static func format(_ date: Date?) -> String {
        guard let date = date else { return "" }

        let now = Date()
        let totalSeconds = date.timeIntervalSince(now)

        if totalSeconds < 0 {
            return "now"
        }

        let totalMinutes = Int(totalSeconds / 60)

        if totalMinutes < 60 {
            return "in \(totalMinutes)m"
        }

        if totalMinutes < 24 * 60 {
            let hours = totalMinutes / 60
            let minutes = totalMinutes % 60
            return "in \(hours)h \(minutes)m"
        }

        let formatter = DateFormatter()
        formatter.dateFormat = "EEE h:mma"
        return formatter.string(from: date).lowercased()
    }
}
