import Foundation

public struct ModelEntry: Sendable {
    public var path: String
    public var backend: String?
    public var languages: [String]?
    public var notes: String?

    init(jsonObject: [String: Any]) {
        path = (jsonObject["path"] as? String) ?? ""
        backend = jsonObject["backend"] as? String
        languages = jsonObject["languages"] as? [String]
        notes = jsonObject["notes"] as? String
    }
}

public struct RuntimeConfig: Sendable {
    public var port: UInt16 = 8477
    public var idleUnloadSeconds: Int = 900
    /// Max length of one part when a long batch transcription is split, ms.
    /// Interactive requests are never split.
    public var batchChunkMs: Int = 10_000
    public var models: [String: ModelEntry] = [:]

    /// Decodes with every top-level key optional: a partial JSON config falls
    /// back to the defaults above (synthesised Codable would require all keys
    /// to be optional types, which leaks into every caller).
    public init(jsonObject: [String: Any]) {
        port = (jsonObject["port"] as? NSNumber)?.uint16Value ?? 8477
        idleUnloadSeconds = (jsonObject["idleUnloadSeconds"] as? NSNumber)?.intValue ?? 900
        batchChunkMs = (jsonObject["batchChunkMs"] as? NSNumber)?.intValue ?? 10_000
        if let rawModels = jsonObject["models"] as? [String: [String: Any]] {
            models = rawModels.mapValues(ModelEntry.init(jsonObject:))
        }
    }

    public static func load(at url: URL) throws -> RuntimeConfig {
        guard FileManager.default.fileExists(atPath: url.path) else {
            throw RuntimeError.message("no config at \(url.path); write one with {\"models\":{...}}")
        }
        let data = try Data(contentsOf: url)
        let object = try JSONSerialization.jsonObject(with: data, options: [])
        guard let top = object as? [String: Any] else {
            throw RuntimeError.message("config root must be a JSON object")
        }
        var config = RuntimeConfig(jsonObject: top)
        guard config.batchChunkMs > 0 else {
            throw RuntimeError.message("batchChunkMs must be > 0, got \(config.batchChunkMs)")
        }
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
