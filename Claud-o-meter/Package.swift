// swift-tools-version: 5.9
import PackageDescription

let package = Package(
    name: "Claud-o-meter",
    platforms: [.macOS(.v13)],
    targets: [
        .executableTarget(
            name: "Claud-o-meter",
            path: "Sources",
            resources: [
                .copy("../Resources/Info.plist")
            ]
        ),
        .testTarget(
            name: "ClaudOMeterTests",
            dependencies: ["Claud-o-meter"],
            path: "Tests"
        )
    ]
)
