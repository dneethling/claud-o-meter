import ActivityKit
import Foundation

// The Live Activity's data contract. `ContentState` is what the relay pushes on
// every update (see `ios_relay.py --self-test` for a live example of the JSON).
//
// IMPORTANT: the property names are snake_case on purpose. ActivityKit decodes
// the APNs `content-state` with a plain JSONDecoder (no keyDecodingStrategy), so
// these names must match the relay's JSON keys exactly. Do not "Swiftify" them
// to camelCase without adding CodingKeys — the push would silently fail to decode.
struct ClaudeUsageAttributes: ActivityAttributes {
    public struct ContentState: Codable, Hashable {
        var session_pct: Int?
        var session_color: String
        var session_reset: String

        var weekly_pct: Int?
        var weekly_color: String
        var weekly_reset: String

        var models: [ModelUsage]
        var credits: Credits?
        var on_credits: Bool

        var status: String        // "ok" | "reauth" | "error" | "starting"
        var note: String          // short human message when status != ok
        var updated_epoch: Int    // unix seconds the relay stamped at send time

        static let placeholder = ContentState(
            session_pct: nil, session_color: "gray", session_reset: "",
            weekly_pct: nil, weekly_color: "gray", weekly_reset: "",
            models: [], credits: nil, on_credits: false,
            status: "starting", note: "Waiting for the relay…", updated_epoch: 0)
    }

    struct ModelUsage: Codable, Hashable, Identifiable {
        var name: String
        var pct: Int
        var color: String
        var reset: String
        var id: String { name }
    }

    struct Credits: Codable, Hashable {
        var enabled: Bool
        var used: String
        var limit: String
        var pct: Int?
    }

    // Static for the life of the activity (shown as the pill's identity).
    var title: String
}
