// swift-tools-version: 6.0
import PackageDescription

let package = Package(
    name: "SpeechRuntimeMenu",
    defaultLocalization: "en",
    platforms: [.macOS(.v15)],
    products: [.executable(name: "SpeechRuntimeApp", targets: ["SpeechRuntimeApp"])],
    targets: [
        .target(name: "RuntimeUIModel"),
        .target(name: "ProcessMetrics", publicHeadersPath: "include"),
        .executableTarget(
            name: "SpeechRuntimeApp", dependencies: ["RuntimeUIModel", "ProcessMetrics"],
            resources: [.process("Resources")]
        ),
        .testTarget(name: "RuntimeUIModelTests", dependencies: ["RuntimeUIModel"]),
    ],
    swiftLanguageModes: [.v5]
)
