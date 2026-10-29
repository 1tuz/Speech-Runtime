// swift-tools-version: 6.0
import PackageDescription

// Тонкая обёртка над FluidAudio: батч-прогон ASR и подача PCM по часам.
// Сам FluidAudio не форкается — зависимость указывает на закреплённую копию
// в experiments/corpus/local/tools/FluidAudio (v0.17.7).
let package = Package(
    name: "FluidASRRunner",
    platforms: [.macOS(.v15)],
    dependencies: [
        .package(path: "../../corpus/local/tools/FluidAudio")
    ],
    targets: [
        .executableTarget(
            name: "fluid-asr",
            dependencies: [.product(name: "FluidAudio", package: "FluidAudio")]
        )
    ]
)
