// Claud-o-meter/Tests/UsageDataTests.swift
import XCTest
@testable import Claud_o_meter

final class UsageDataTests: XCTestCase {

    let sampleJSON = """
    {
        "five_hour": {"utilization": 24.0, "resets_at": "2026-04-14T14:59:59.793717+00:00"},
        "seven_day": {"utilization": 14.0, "resets_at": "2026-04-20T13:00:00.793738+00:00"},
        "seven_day_oauth_apps": null,
        "seven_day_opus": null,
        "seven_day_sonnet": {"utilization": 2.0, "resets_at": "2026-04-20T14:00:00.793747+00:00"},
        "seven_day_cowork": null,
        "seven_day_omelette": {"utilization": 0.0, "resets_at": null},
        "iguana_necktie": null,
        "extra_usage": {"is_enabled": false, "monthly_limit": null, "used_credits": null, "utilization": null, "currency": null}
    }
    """.data(using: .utf8)!

    func testDecodesSessionMetric() throws {
        let usage = try JSONDecoder.apiDecoder.decode(UsageData.self, from: sampleJSON)
        XCTAssertEqual(usage.session?.utilization, 24.0)
        XCTAssertNotNil(usage.session?.resetsAt)
    }

    func testDecodesWeeklyAllModels() throws {
        let usage = try JSONDecoder.apiDecoder.decode(UsageData.self, from: sampleJSON)
        XCTAssertEqual(usage.weeklyAll?.utilization, 14.0)
    }

    func testDecodesSonnet() throws {
        let usage = try JSONDecoder.apiDecoder.decode(UsageData.self, from: sampleJSON)
        XCTAssertEqual(usage.weeklySonnet?.utilization, 2.0)
    }

    func testDecodesNullOpusAsNil() throws {
        let usage = try JSONDecoder.apiDecoder.decode(UsageData.self, from: sampleJSON)
        XCTAssertNil(usage.weeklyOpus)
    }

    func testDecodesExtraUsageDisabled() throws {
        let usage = try JSONDecoder.apiDecoder.decode(UsageData.self, from: sampleJSON)
        XCTAssertEqual(usage.extraUsage?.isEnabled, false)
        XCTAssertNil(usage.extraUsage?.utilization)
    }

    func testDecodesNullResetsAtAsNil() throws {
        let usage = try JSONDecoder.apiDecoder.decode(UsageData.self, from: sampleJSON)
        XCTAssertNotNil(usage.session?.resetsAt)
    }

    func testHighestUtilization() throws {
        let usage = try JSONDecoder.apiDecoder.decode(UsageData.self, from: sampleJSON)
        XCTAssertEqual(usage.highestUtilization, 24.0)
    }
}
