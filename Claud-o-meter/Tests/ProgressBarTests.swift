// Claud-o-meter/Tests/ProgressBarTests.swift
import XCTest
@testable import Claud_o_meter

final class ProgressBarTests: XCTestCase {

    func testZeroPercent() {
        let bar = ProgressBar.render(percent: 0, width: 10)
        XCTAssertEqual(bar, "░░░░░░░░░░")
    }

    func testHundredPercent() {
        let bar = ProgressBar.render(percent: 100, width: 10)
        XCTAssertEqual(bar, "██████████")
    }

    func testFiftyPercent() {
        let bar = ProgressBar.render(percent: 50, width: 10)
        XCTAssertEqual(bar, "█████░░░░░")
    }

    func testOverHundredClamps() {
        let bar = ProgressBar.render(percent: 150, width: 10)
        XCTAssertEqual(bar, "██████████")
    }

    func testNegativeClamps() {
        let bar = ProgressBar.render(percent: -10, width: 10)
        XCTAssertEqual(bar, "░░░░░░░░░░")
    }

    func testDefaultWidth() {
        let bar = ProgressBar.render(percent: 50)
        XCTAssertEqual(bar.count, 20) // default width is 20
    }

    func testColorForPercentGreen() {
        let color = ProgressBar.color(for: 30)
        XCTAssertEqual(color, .green)
    }

    func testColorForPercentOrange() {
        let color = ProgressBar.color(for: 65)
        XCTAssertEqual(color, .orange)
    }

    func testColorForPercentRed() {
        let color = ProgressBar.color(for: 90)
        XCTAssertEqual(color, .red)
    }
}
