// Claud-o-meter/Tests/TimeFormatterTests.swift
import XCTest
@testable import Claud_o_meter

final class TimeFormatterTests: XCTestCase {

    func testMinutesOnly() {
        let future = Date().addingTimeInterval(45 * 60)
        let result = ResetTimeFormatter.format(future)
        XCTAssertTrue(result.hasPrefix("in "), "Expected 'in Xm', got '\(result)'")
        XCTAssertTrue(result.hasSuffix("m"), "Expected 'in Xm', got '\(result)'")
    }

    func testHoursAndMinutes() {
        let future = Date().addingTimeInterval(3 * 3600 + 14 * 60)
        let result = ResetTimeFormatter.format(future)
        XCTAssertTrue(result.contains("h"), "Expected hours in '\(result)'")
        XCTAssertTrue(result.contains("m"), "Expected minutes in '\(result)'")
        XCTAssertTrue(result.hasPrefix("in "))
    }

    func testPastDateReturnsNow() {
        let past = Date().addingTimeInterval(-60)
        let result = ResetTimeFormatter.format(past)
        XCTAssertEqual(result, "now")
    }

    func testMoreThan24HoursShowsDayTime() {
        let future = Date().addingTimeInterval(36 * 3600)
        let result = ResetTimeFormatter.format(future)
        XCTAssertFalse(result.hasPrefix("in "), "More than 24h should show day+time, got '\(result)'")
        XCTAssertTrue(result.contains(":"), "Expected time with colon, got '\(result)'")
    }

    func testNilDateReturnsEmptyString() {
        let result = ResetTimeFormatter.format(nil)
        XCTAssertEqual(result, "")
    }
}
