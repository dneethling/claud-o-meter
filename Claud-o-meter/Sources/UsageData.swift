// Claud-o-meter/Sources/UsageData.swift
import Foundation

struct Metric: Codable, Sendable {
    let utilization: Double
    let resetsAt: Date?

    enum CodingKeys: String, CodingKey {
        case utilization
        case resetsAt = "resets_at"
    }
}

struct ExtraUsage: Codable, Sendable {
    let isEnabled: Bool
    let monthlyLimit: Double?
    let usedCredits: Double?
    let utilization: Double?

    enum CodingKeys: String, CodingKey {
        case isEnabled = "is_enabled"
        case monthlyLimit = "monthly_limit"
        case usedCredits = "used_credits"
        case utilization
    }
}

struct UsageData: Codable, Sendable {
    let session: Metric?
    let weeklyAll: Metric?
    let weeklySonnet: Metric?
    let weeklyOpus: Metric?
    let extraUsage: ExtraUsage?

    enum CodingKeys: String, CodingKey {
        case session = "five_hour"
        case weeklyAll = "seven_day"
        case weeklySonnet = "seven_day_sonnet"
        case weeklyOpus = "seven_day_opus"
        case extraUsage = "extra_usage"
    }

    var highestUtilization: Double {
        [session?.utilization, weeklyAll?.utilization, weeklySonnet?.utilization, weeklyOpus?.utilization]
            .compactMap { $0 }
            .max() ?? 0
    }
}

extension JSONDecoder {
    static let apiDecoder: JSONDecoder = {
        let decoder = JSONDecoder()
        let formatter = ISO8601DateFormatter()
        formatter.formatOptions = [.withInternetDateTime, .withFractionalSeconds]
        let fallbackFormatter = ISO8601DateFormatter()
        fallbackFormatter.formatOptions = [.withInternetDateTime]
        decoder.dateDecodingStrategy = .custom { decoder in
            let container = try decoder.singleValueContainer()
            let string = try container.decode(String.self)
            if let date = formatter.date(from: string) { return date }
            if let date = fallbackFormatter.date(from: string) { return date }
            throw DecodingError.dataCorruptedError(in: container, debugDescription: "Invalid date: \(string)")
        }
        return decoder
    }()
}
