// swift-tools-version: 6.0
import PackageDescription

let package = Package(
    name: "SpeechRuntime",
    platforms: [.macOS(.v15)],
    products: [
        .executable(name: "speech-runtime", targets: ["SpeechRuntimeDaemon"]),
        .executable(name: "sr", targets: ["SRCLI"]),
        .library(name: "SpeechRuntimeCore", targets: ["SpeechRuntimeCore"]),
    ],
    targets: [
        .target(name: "SpeechRuntimeCore"),
        .executableTarget(name: "SpeechRuntimeDaemon", dependencies: ["SpeechRuntimeCore"]),
        .executableTarget(name: "SRCLI", dependencies: ["SpeechRuntimeCore"]),
        .testTarget(name: "SpeechRuntimeCoreTests", dependencies: ["SpeechRuntimeCore"]),
    ],
    swiftLanguageModes: [.v5]
)
