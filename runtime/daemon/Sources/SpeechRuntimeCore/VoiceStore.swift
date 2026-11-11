import Foundation

/// Voice profiles registered with the shared runtime: one normalized 16 kHz
/// WAV plus the transcript the clone needs, stored under the runtime support
/// directory so every client sees the same set.
public actor VoiceStore {
    public struct Voice: Sendable {
        public var id: String
        public var name: String
        public var language: String?
        public var referenceText: String
        public var audioPath: String
        public var createdAt: String

        var json: [String: Any] {
            [
                "id": id,
                "name": name,
                "language": language ?? NSNull(),
                "referenceText": referenceText,
                "audioPath": audioPath,
                "createdAt": createdAt,
            ]
        }
    }

    private let directory: URL
    private var voices: [String: Voice] = [:]

    public init(directory: URL = Paths.support.appendingPathComponent("voices", isDirectory: true)) {
        self.directory = directory
        self.voices = VoiceStore.read(directory: directory)
    }

    public func list() -> [[String: Any]] {
        voices.values.sorted { $0.createdAt < $1.createdAt }.map(\.json)
    }

    public func get(_ id: String) -> Voice? { voices[id] }

    public func register(
        name: String, language: String?, referenceText: String, wav: Data
    ) throws -> [String: Any] {
        let trimmed = name.trimmingCharacters(in: .whitespacesAndNewlines)
        guard !trimmed.isEmpty else {
            throw RuntimeError.message("voice name is required")
        }
        guard Self.looksLikeWAV(wav) else {
            throw RuntimeError.message("voice audio must be a PCM WAV file")
        }
        try FileManager.default.createDirectory(
            at: directory, withIntermediateDirectories: true)
        let id = UUID().uuidString.lowercased()
        let path = directory.appendingPathComponent("\(id).wav")
        try wav.write(to: path)
        let voice = Voice(
            id: id,
            name: trimmed,
            language: language,
            referenceText: referenceText,
            audioPath: path.path,
            createdAt: ISO8601DateFormatter().string(from: Date()))
        voices[id] = voice
        persist()
        return voice.json
    }

    public func delete(_ id: String) throws -> Bool {
        guard let voice = voices.removeValue(forKey: id) else { return false }
        try? FileManager.default.removeItem(atPath: voice.audioPath)
        persist()
        return true
    }

    private func persist() {
        let rows = voices.values.map(\.json)
        if let data = try? JSONSerialization.data(
            withJSONObject: rows, options: [.prettyPrinted, .sortedKeys])
        {
            try? data.write(to: directory.appendingPathComponent("index.json"))
        }
    }

    private static func read(directory: URL) -> [String: Voice] {
        try? FileManager.default.createDirectory(
            at: directory, withIntermediateDirectories: true)
        guard let data = try? Data(contentsOf: directory.appendingPathComponent("index.json")),
              let rows = try? JSONSerialization.jsonObject(with: data) as? [[String: Any]]
        else { return [:] }
        var voices: [String: Voice] = [:]
        for row in rows {
            guard let id = row["id"] as? String,
                  let audioPath = row["audioPath"] as? String,
                  FileManager.default.fileExists(atPath: audioPath)
            else { continue }
            voices[id] = Voice(
                id: id,
                name: row["name"] as? String ?? "Voice",
                language: row["language"] as? String,
                referenceText: row["referenceText"] as? String ?? "",
                audioPath: audioPath,
                createdAt: row["createdAt"] as? String ?? "")
        }
        return voices
    }

    private static func looksLikeWAV(_ data: Data) -> Bool {
        data.count >= 44
            && data[0..<4].elementsEqual(Array("RIFF".utf8))
            && data[8..<12].elementsEqual(Array("WAVE".utf8))
    }
}
