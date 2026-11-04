import Foundation

public struct ModelEntry: Sendable, Codable {
    public var path: String
    public var backend: String?
    public var languages: [String]?
    public var notes: String?
}

public struct RuntimeConfig: Sendable, Codable {
    public var port: UInt16 = 8477
    public var idleUnloadSeconds: Int = 900
    public var models: [String: ModelEntry] = [:]

    public static func load(at url: URL) throws -> RuntimeConfig {
        guard FileManager.default.fileExists(atPath: url.path) else {
            throw RuntimeError.message("no config at \(url.path); write one with {\"models\":{...}}")
        }
        let data = try Data(contentsOf: url)
        var config = try JSONDecoder().decode(RuntimeConfig.self, from: data)
        config.models = config.models.mapValues { entry in
            var entry = entry
            entry.path = (entry.path as NSString).expandingTildeInPath
            return entry
        }
        return config
    }
}

public enum Paths {
    public static var support: URL {
        FileManager.default.homeDirectoryForCurrentUser
            .appendingPathComponent("Library/Application Support/SpeechRuntime", isDirectory: true)
    }

    public static var token: URL { support.appendingPathComponent("token") }
    public static var config: URL { support.appendingPathComponent("config.json") }
    public static var log: URL { support.appendingPathComponent("daemon.log") }

    public static func workerBinary() -> String {
        if let explicit = ProcessInfo.processInfo.environment["SPEECH_RUNTIME_WORKER"] {
            return explicit
        }
        let nextToDaemon = Bundle.main.bundlePath
        if nextToDaemon.hasSuffix(".app") {
            return nextToDaemon + "/Contents/MacOS/sr-worker"
        }
        let directory = URL(fileURLWithPath: CommandLine.arguments[0]).deletingLastPathComponent()
        return directory.appendingPathComponent("sr-worker").path
    }
}
