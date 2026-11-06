import Foundation

/// Mirrors the existing /health contract. Unknown counters stay absent, not zero.
public struct RuntimeSnapshot: Decodable, Sendable {
    public let ok: Bool
    public let version: String?
    public let streams: Int?
    public let uptime: Double?
    public let memory: Memory?
    public let worker: Worker?
    enum CodingKeys: String, CodingKey { case ok, version, streams, memory, worker; case uptime = "uptime_s" }

    public struct Memory: Decodable, Sendable {
        public let pid: Int32?
        public let resident: UInt64?
        public let footprint: UInt64?
        public let neural: UInt64?
        enum CodingKeys: String, CodingKey {
            case pid
            case resident = "resident_bytes"
            case footprint = "phys_footprint_bytes"
            case neural = "neural_bytes"
        }
    }
    public struct Worker: Decodable, Sendable {
        public let pid: Int32?
        public let restarts: Int?
        public let error: String?
        public let memory: Memory?
        public let models: [Model]?
    }
    public struct Model: Decodable, Sendable {
        public let model: String
        public let loaded: Bool?
        public let device: String?
        public let inflight: Int?
        public let interactive: Int?
        public let batch: Int?
        public let done: Int?
        enum CodingKeys: String, CodingKey {
            case model, loaded, device, inflight, done
            case interactive = "queued_interactive"
            case batch = "queued_batch"
        }
    }
    public var selectedModel: Model? { worker?.models?.first { $0.model == ModelSpec.id } }
    public var modelLoaded: Bool { selectedModel?.loaded == true }
    public var processing: Bool { (selectedModel?.inflight ?? 0) > 0 }
    public var hasWorker: Bool { (worker?.pid ?? 0) > 0 && worker?.error == nil }
}

public enum ModelSpec {
    public static let id = "parakeet-ultra"
    public static let filename = "parakeet-ultra-Q8_0.gguf"
    public static let repository = "handy-computer/parakeet-ultra-gguf"
    public static let revision = "a1539be1b849ea7463b88343e5277b373d0c4ea9"
    public static let expectedBytes: Int64 = 740_363_168
    public static let fileURL = URL(string: "https://huggingface.co/\(repository)/resolve/\(revision)/\(filename)")!
    public static let metadataURL = URL(string: "https://huggingface.co/api/models/\(repository)/tree/\(revision)")!
}

/// CPU in Activity Monitor units: 100% = one fully occupied logical core.
/// Account for pid reuse / worker restart and never turn a missing sample into 0%.
public struct CPURate: Sendable {
    private var last: (identity: String, time: Double, nanos: UInt64)?
    public init() {}
    public mutating func sample(identity: String, time: Double, totalNanos: UInt64) -> Double? {
        defer { last = (identity, time, totalNanos) }
        guard let old = last, old.identity == identity,
              time > old.time, totalNanos >= old.nanos else { return nil }
        return Double(totalNanos - old.nanos) / 1_000_000_000 / (time - old.time) * 100
    }
}

public enum RuntimeFiles {
    public static func agentPlist(binary: String, worker: String, config: String, token: String, label: String, idleSeconds: Int = 900) -> [String: Any] {
        [
            "Label": label,
            "ProgramArguments": [binary, "--config", config, "--token-file", token, "--worker", worker, "--idle-unload", String(idleSeconds)],
            "RunAtLoad": true,
            "KeepAlive": ["SuccessfulExit": false],
            "ThrottleInterval": 10,
            "ProcessType": "Adaptive",
            "StandardOutPath": "/dev/null",
            "StandardErrorPath": "/dev/null",
        ]
    }
    public static func modelConfig(path: String, idleSeconds: Int = 900) -> [String: Any] {
        ["port": 8477, "idleUnloadSeconds": idleSeconds, "batchChunkMs": 10_000,
         "models": [ModelSpec.id: ["path": path, "backend": "ane", "languages": ["ru", "en"]]]]
    }
}
