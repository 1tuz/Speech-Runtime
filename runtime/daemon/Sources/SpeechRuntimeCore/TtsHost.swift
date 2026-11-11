import Foundation

/// Hosts the NDJSON TTS sidecar (`soniqo-tts-sidecar`) that Speech Studio also
/// uses. The daemon owns at most one such process: every app that routes TTS
/// through the shared runtime reuses the same resident model instead of loading
/// its own copy. The sidecar protocol is line-delimited JSON with
/// `{id, command, ...}` requests and `{id, ok, result|error}` replies.
public actor TtsHost {
    private struct Pending {
        var continuation: CheckedContinuation<[String: Any], Error>
        var requestId: String?
    }

    private let binaryPath: String
    private let log: @Sendable (String) -> Void
    private var process: Process?
    private var stdinHandle: FileHandle?
    private var reader: Task<Void, Never>?
    private var nextID: UInt64 = 1
    private var pending: [UInt64: Pending] = [:]
    private var lineBuffer = Data()
    private var loadedEngine: String?
    private var loadedModelId: String?
    private var loadedVariant: String?
    private var reloads = 0
    /// Latest `[sidecar] mem …` snapshot parsed from the sidecar's stderr.
    private var memory: [String: Any]?
    /// Updated on load and synthesize; the daemon's idle monitor uses it to
    /// unload the resident TTS model after `idleUnloadSeconds` of silence.
    private var lastTtsActivity = Date()
    /// Number of requests awaiting a sidecar reply; guards idle unload.
    private var inFlight = 0

    public init(binaryPath: String, log: @escaping @Sendable (String) -> Void) {
        self.binaryPath = (binaryPath as NSString).expandingTildeInPath
        self.log = log
    }

    public var pid: Int32? { process?.processIdentifier }
    public var loaded: (engine: String?, modelId: String?) { (loadedEngine, loadedModelId) }
    public var residentVariant: String? { loadedVariant }
    public var restartCount: Int { reloads }
    public var memoryReport: [String: Any]? { memory }

    /// Unloads the resident model when no request is in flight. Returns true
    /// if an unload was issued (or nothing was resident); false when a
    /// synthesis is still running and the caller should retry later.
    @discardableResult
    public func unloadIdleTTS() async -> Bool {
        guard process?.isRunning == true else { return true }
        guard inFlight == 0, loadedEngine != nil else { return true }
        _ = try? await unload(model: "")
        return true
    }

    /// Seconds since the last TTS load or synthesize request.
    public var ttsIdleSeconds: TimeInterval {
        Date().timeIntervalSince(lastTtsActivity)
    }

    private func ensureStarted() throws {
        if let process, process.isRunning { return }
        stop()
        guard FileManager.default.isExecutableFile(atPath: binaryPath) else {
            throw RuntimeError.message(
                "TTS sidecar not found at \(binaryPath); set tts.binary in the runtime config")
        }
        let child = Process()
        child.executableURL = URL(fileURLWithPath: binaryPath)
        let input = Pipe()
        let output = Pipe()
        let errors = Pipe()
        child.standardInput = input
        child.standardOutput = output
        child.standardError = errors
        try child.run()
        process = child
        stdinHandle = input.fileHandleForWriting
        reloads += 1
        let log = self.log
        let errorHandle = errors.fileHandleForReading
        Task.detached { [weak self] in
            while true {
                let chunk = errorHandle.availableData
                if chunk.isEmpty { return }
                if let text = String(data: chunk, encoding: .utf8) {
                    for line in text.split(separator: "\n") {
                        log("[tts] \(line)")
                        await self?.parseMemoryLine(String(line))
                    }
                }
            }
        }
        let handle = output.fileHandleForReading
        reader = Task { [weak self] in
            while true {
                let chunk = handle.availableData
                if chunk.isEmpty { break }
                await self?.consume(chunk)
            }
            await self?.processExited()
        }
        log("[tts] started \(binaryPath) pid=\(child.processIdentifier)")
    }

    public func stop() {
        stdinHandle?.closeFile()
        stdinHandle = nil
        if let process {
            process.terminate()
        }
        process = nil
        reader?.cancel()
        reader = nil
        lineBuffer.removeAll()
        loadedEngine = nil
        loadedModelId = nil
        loadedVariant = nil
        memory = nil
    }

    private func consume(_ chunk: Data) {
        lineBuffer.append(chunk)
        while let newline = lineBuffer.firstIndex(of: 0x0A) {
            let line = String(decoding: lineBuffer[..<newline], as: UTF8.self)
            lineBuffer.removeSubrange(lineBuffer.startIndex...newline)
            handle(line: line)
        }
    }

    private func handle(line: String) {
        guard let data = line.data(using: .utf8),
              let object = try? JSONSerialization.jsonObject(with: data) as? [String: Any],
              let id = (object["id"] as? String).flatMap(Self.parseID),
              let pending = pending.removeValue(forKey: id)
        else {
            if !line.isEmpty { log("[tts] unparsed: \(line.prefix(200))") }
            return
        }
        if object["ok"] as? Bool == true {
            pending.continuation.resume(returning: object["result"] as? [String: Any] ?? [:])
        } else {
            let message = object["error"] as? String ?? "TTS sidecar request failed"
            pending.continuation.resume(throwing: RuntimeError.message(message))
        }
    }

    private static func parseID(_ raw: String) -> UInt64? {
        raw.hasPrefix("runtime-") ? UInt64(raw.dropFirst("runtime-".count)) : UInt64(raw)
    }

    private func processExited() {
        log("[tts] sidecar exited")
        process = nil
        stdinHandle = nil
        loadedEngine = nil
        loadedModelId = nil
        loadedVariant = nil
        memory = nil
        let waiting = pending.values
        pending.removeAll()
        inFlight = 0
        for entry in waiting {
            entry.continuation.resume(
                throwing: RuntimeError.message("TTS sidecar exited during the request"))
        }
    }

    /// Parses `[sidecar] mem <label> active=..M cache=..M peak=..M rss=..M
    /// footprint=..M` from the sidecar's stderr and keeps the newest sample
    /// (rss, footprint, peak) for `/health` and the TTS model listing.
    private func parseMemoryLine(_ raw: String) {
        guard raw.hasPrefix("[sidecar] mem ") else { return }
        var rss: Int?, footprint: Int?, peak: Int?
        for token in raw.split(separator: " ").dropFirst(2) {
            guard let equals = token.firstIndex(of: "=") else { continue }
            let key = token[..<equals]
            let value = Int(token[token.index(after: equals)...].dropLast())
            switch key {
            case "rss": rss = value
            case "footprint": footprint = value
            case "peak": peak = value
            default: break
            }
        }
        guard rss != nil || footprint != nil || peak != nil else { return }
        var report: [String: Any] = [:]
        if let rss { report["rssMb"] = rss }
        if let footprint { report["footprintMb"] = footprint }
        if let peak { report["peakMb"] = peak }
        memory = report
    }

    private func request(
        command: String,
        fields: [String: Any],
        requestId: String? = nil,
        timeout: TimeInterval = 300
    ) async throws -> [String: Any] {
        try ensureStarted()
        nextID += 1
        let id = nextID
        let wireID = "runtime-\(id)"
        var body = fields
        body["id"] = wireID
        body["command"] = command
        let payload = try JSONSerialization.data(withJSONObject: body)
        inFlight += 1
        defer { inFlight -= 1 }
        return try await withCheckedThrowingContinuation { continuation in
            pending[id] = Pending(continuation: continuation, requestId: requestId)
            Task {
                try await Task.sleep(nanoseconds: UInt64(timeout * 1_000_000_000))
                await self.timeout(id: id)
            }
            do {
                guard let stdinHandle else {
                    throw RuntimeError.message("TTS sidecar stdin is closed")
                }
                try stdinHandle.write(contentsOf: payload + Data([0x0A]))
            } catch {
                self.pending.removeValue(forKey: id)?.continuation.resume(throwing: error)
            }
        }
    }

    private func timeout(id: UInt64) {
        guard let entry = pending.removeValue(forKey: id) else { return }
        entry.continuation.resume(throwing: RuntimeError.message("TTS sidecar request timed out"))
    }

    // MARK: - Model lifecycle

    public func load(model: String, entry: TtsModelEntry, variant: String?) async throws -> [String: Any] {
        try ensureStarted()
        let modelId = entry.modelId
        if loadedEngine == entry.engine, loadedModelId == modelId,
           variant == nil || variant == loadedVariant {
            lastTtsActivity = Date()
            return ["loaded": true, "modelId": modelId, "reused": true]
        }
        var fields: [String: Any] = ["engine": entry.engine, "modelId": modelId]
        if let variant { fields["variant"] = variant }
        let result = try await request(command: "init_model", fields: fields, timeout: 1800)
        loadedEngine = entry.engine
        loadedModelId = modelId
        loadedVariant = variant
        lastTtsActivity = Date()
        return result
    }

    public func unload(model: String) async throws -> [String: Any] {
        guard process?.isRunning == true else { return ["loaded": false] }
        guard let engine = loadedEngine else { return ["loaded": false] }
        let result = try await request(
            command: "unload_model", fields: ["engine": engine])
        loadedEngine = nil
        loadedModelId = nil
        loadedVariant = nil
        return result
    }

    public func info(model: String, entry: TtsModelEntry) async throws -> [String: Any] {
        try ensureStarted()
        return try await request(
            command: "model_info",
            fields: ["engine": entry.engine, "modelId": entry.modelId])
    }

    public func stats() async throws -> [String: Any] {
        guard process?.isRunning == true else {
            return ["loaded": false, "pid": -1, "restarts": reloads]
        }
        let info = try await request(
            command: "ping", fields: [:], timeout: 30)
        return [
            "loaded": loadedEngine != nil,
            "pid": process?.processIdentifier ?? -1,
            "restarts": reloads,
            "engine": loadedEngine ?? NSNull(),
            "modelId": loadedModelId ?? NSNull(),
            "loadedVariant": loadedVariant ?? NSNull(),
            "memory": memory ?? NSNull(),
            "sidecar": info,
        ]
    }

    // MARK: - Synthesis

    public func synthesize(
        model: String,
        entry: TtsModelEntry,
        text: String,
        language: String?,
        seed: UInt64?,
        referenceAudioPath: String?,
        referenceText: String?,
        requestId: String?,
        variant: String?
    ) async throws -> [String: Any] {
        if loadedModelId != entry.modelId {
            _ = try await load(model: model, entry: entry, variant: variant)
        }
        lastTtsActivity = Date()
        guard let referenceAudioPath, !referenceAudioPath.isEmpty else {
            throw RuntimeError.message("TTS needs a voice profile or reference audio")
        }
        let command: String
        var fields: [String: Any] = [
            "engine": entry.engine,
            "modelId": entry.modelId,
            "text": text,
            "referenceAudioPath": referenceAudioPath,
        ]
        switch entry.engine {
        case "qwen3":
            command = "synthesize_icl"
            fields["referenceText"] = referenceText ?? ""
            fields["language"] = language ?? "ru"
        case "voxcpm2":
            command = "synthesize_voxcpm2"
            if let referenceText { fields["referenceText"] = referenceText }
            if let language { fields["language"] = language }
        case "espeech":
            command = "synthesize_espeech"
            fields["referenceText"] = referenceText ?? ""
            fields["language"] = "ru"
        default:
            throw RuntimeError.message("unsupported TTS engine \(entry.engine)")
        }
        if let seed { fields["seed"] = seed }
        if let variant { fields["variant"] = variant }
        let result = try await request(
            command: command, fields: fields, requestId: requestId, timeout: 900)
        guard let audioPath = result["audioPath"] as? String else {
            throw RuntimeError.message("TTS sidecar returned no audioPath")
        }
        let wav = try Data(contentsOf: URL(fileURLWithPath: audioPath))
        let sampleRate = (result["sampleRate"] as? NSNumber)?.intValue ?? entry.sampleRate
        return [
            "audioBase64": wav.base64EncodedString(),
            "sampleRate": sampleRate,
            "durationSec": Self.wavDurationSec(wav) ?? 0,
            "model": model,
            "engine": entry.engine,
        ]
    }

    /// Cancels queued requests for `requestId`. MLX sampling itself is not
    /// interruptible inside the sidecar, so an already-running take finishes
    /// and its result is discarded; the model stays resident.
    public func cancel(requestId: String) -> Int {
        let ids = pending.filter { $0.value.requestId == requestId }.map(\.key)
        for id in ids {
            pending.removeValue(forKey: id)?.continuation.resume(
                throwing: RuntimeError.message("request cancelled"))
        }
        return ids.count
    }

    private static func wavDurationSec(_ data: Data) -> Double? {
        guard data.count >= 44,
              String(data: data[0..<4], encoding: .ascii) == "RIFF",
              String(data: data[8..<12], encoding: .ascii) == "WAVE"
        else { return nil }
        var offset = 12
        var byteRate = 0
        var dataBytes = 0
        while offset + 8 <= data.count {
            let id = String(decoding: data[offset..<offset + 4], as: UTF8.self)
            let size = data[offset + 4..<offset + 8].withUnsafeBytes {
                $0.loadUnaligned(as: UInt32.self)
            }
            offset += 8
            if id == "fmt " , offset + 12 <= data.count {
                byteRate = Int(data[offset + 8..<offset + 12].withUnsafeBytes {
                    $0.loadUnaligned(as: UInt32.self)
                })
            } else if id == "data" {
                dataBytes = Int(size)
                break
            }
            offset += Int(size) + Int(size % 2)
        }
        guard byteRate > 0 else { return nil }
        return Double(dataBytes) / Double(byteRate)
    }
}
