import Foundation

/// HTTP surface. Kept deliberately small: models, one-shot transcription, streaming sessions and
/// health. Every response is JSON; audio goes in as a body, never in a URL.
public struct Router: Sendable {
    public let token: AuthToken
    public let worker: WorkerClient
    public let config: @Sendable () -> RuntimeConfig
    public let idle: IdlePolicy
    public let startedAt = Date()
    public let log: @Sendable (String) -> Void
    private let sessions = SessionTable()

    public init(
        token: AuthToken,
        worker: WorkerClient,
        config: @escaping @Sendable () -> RuntimeConfig,
        idle: IdlePolicy,
        log: @escaping @Sendable (String) -> Void
    ) {
        self.token = token
        self.worker = worker
        self.config = config
        self.idle = idle
        self.log = log
    }

    public func handle(_ request: HTTPRequest) async -> HTTPResponse {
        guard request.path == "/health" || token.matches(request.headers["authorization"]) else {
            return .error("missing or invalid bearer token", status: 401)
        }
        do {
            switch (request.method, request.path) {
            case ("GET", "/health"): return try await health()
            case ("GET", "/v1/models"): return try await models()
            case ("POST", "/v1/models/load"): return try await load(request)
            case ("POST", "/v1/models/unload"): return try await unload(request)
            case ("POST", "/v1/audio/transcriptions"): return try await transcribe(request)
            case ("POST", "/v1/streams"): return try await openStream(request)
            case ("POST", "/v1/streams/audio"): return try await streamAudio(request)
            case ("POST", "/v1/streams/end"): return try await endStream(request)
            case ("GET", "/v1/streams/events"): return try await streamEvents(request)
            case ("DELETE", "/v1/streams"): return try await cancelStream(request)
            default: return .error("no route for \(request.method) \(request.path)", status: 404)
            }
        } catch let error as RuntimeError {
            return .error(error.description, status: 400)
        } catch {
            return .error("\(error)", status: 500)
        }
    }

    // MARK: - Models

    private func health() async throws -> HTTPResponse {
        var workerState: [String: Any] = [
            "pid": await worker.pid ?? -1,
            "restarts": await worker.restartCount,
        ]
        do {
            let stats = try await worker.stats()
            workerState["models"] = stats["models"] ?? []
            workerState["sessions"] = stats["sessions"] ?? 0
            workerState["generation"] = await worker.generation
        } catch {
            workerState["error"] = "\(error)"
        }
        for id in await sessions.sweep(now: Date()) { await idle.streamClosed(id) }
        await worker.cleanup()
        return .json([
            "ok": true,
            "version": "0.1.0",
            "uptime_s": Int(Date().timeIntervalSince(startedAt)),
            "worker": workerState,
            "streams": await sessions.activeCount(),
        ])
    }

    private func models() async throws -> HTTPResponse {
        let configured = config().models
        let stats = (try? await worker.stats())?["models"] as? [[String: Any]] ?? []
        var loadedBy: [String: [String: Any]] = [:]
        for entry in stats {
            if let name = entry["model"] as? String { loadedBy[name] = entry }
        }
        var rows: [[String: Any]] = []
        for (name, entry) in configured.sorted(by: { $0.key < $1.key }) {
            var row: [String: Any] = [
                "model": name,
                "path": entry.path,
                "backend": entry.backend ?? "auto",
                "languages": entry.languages ?? [],
            ]
            if let live = loadedBy[name] {
                row["loaded"] = live["loaded"] ?? false
                row["device"] = live["device"] ?? NSNull()
                row["queued_interactive"] = live["queued_interactive"] ?? 0
                row["queued_batch"] = live["queued_batch"] ?? 0
                row["done"] = live["done"] ?? 0
            } else {
                row["loaded"] = false
            }
            rows.append(row)
        }
        return .json(["models": rows])
    }

    private func load(_ request: HTTPRequest) async throws -> HTTPResponse {
        let body = try Self.body(request)
        guard let model = body["model"] as? String else {
            throw RuntimeError.message("field \"model\" is required")
        }
        guard let entry = config().models[model] else {
            throw RuntimeError.message("unknown model \(model); declared models: \(config().models.keys.sorted())")
        }
        let backend = body["backend"] as? String ?? entry.backend
        let result = try await worker.load(model: model, path: entry.path, backend: backend)
        return .json(["model": model, "result": result])
    }

    private func unload(_ request: HTTPRequest) async throws -> HTTPResponse {
        let body = try Self.body(request)
        guard let model = body["model"] as? String else {
            throw RuntimeError.message("field \"model\" is required")
        }
        guard config().models[model] != nil else {
            throw RuntimeError.message("unknown model \(model)")
        }
        return .json(["model": model, "result": try await worker.unload(model: model)])
    }

    // MARK: - One-shot

    private func transcribe(_ request: HTTPRequest) async throws -> HTTPResponse {
        let model = request.query["model"] ?? ((try? Self.body(request))?["model"] as? String)
        guard let model else { throw RuntimeError.message("query parameter \"model\" is required") }
        guard config().models[model] != nil else {
            throw RuntimeError.message("unknown model \(model)")
        }
        let audio = try Audio.decode(body: request.body, rawFormat: request.headers["x-sample-format"])
        if abs(audio.sampleRate - Audio.sampleRate) > 1 {
            throw RuntimeError.message("audio is \(Int(audio.sampleRate)) Hz; send 16000 Hz")
        }
        guard !audio.samples.isEmpty else { throw RuntimeError.message("empty audio") }
        let priority = request.query["priority"] ?? "batch"
        let window = request.query["window_ms"].flatMap(Int.init)
        let started = Date()
        await idle.touch(model)
        let result = try await worker.transcribe(
            pcm: audio.samples,
            model: model,
            language: request.query["language"],
            windowMs: window,
            priority: priority
        )
        log("transcribe model=\(model) audio_ms=\(audio.samples.count * 1000 / 16_000) priority=\(priority)")
        var payload = result
        payload["wall_ms"] = Int(Date().timeIntervalSince(started) * 1000)
        payload["audio_ms"] = audio.samples.count * 1000 / 16_000
        return .json(payload)
    }

    // MARK: - Streaming

    private func openStream(_ request: HTTPRequest) async throws -> HTTPResponse {
        let body = try Self.body(request)
        guard let model = body["model"] as? String else {
            throw RuntimeError.message("field \"model\" is required")
        }
        guard config().models[model] != nil else {
            throw RuntimeError.message("unknown model \(model)")
        }
        var config: [String: Any] = [:]
        for (key, value) in (body["config"] as? [String: Any]) ?? [:] { config[key] = value }
        if let language = body["language"] as? String { config["language"] = language }
        let id = UUID().uuidString
        let result = try await worker.openSession(id: id, model: model, config: config)
        let generation = await worker.generation
        await sessions.insert(id, model: model, config: config, generation: generation)
        await idle.streamOpened(id, model: model, generation: generation)
        return .json(["stream": id, "model": model, "result": result])
    }

    private func streamAudio(_ request: HTTPRequest) async throws -> HTTPResponse {
        let id = try streamID(request)
        try await guardGeneration(id)
        let audio = try Audio.decode(body: request.body, rawFormat: request.headers["x-sample-format"])
        guard !audio.samples.isEmpty else { throw RuntimeError.message("empty audio") }
        try await worker.sessionAudio(id: id, pcm: audio.samples)
        let total = await sessions.addedSamples(id, count: audio.samples.count)
        await worker.cleanup()
        return .json(["stream": id, "samples": total, "audio_ms": total * 1000 / 16_000])
    }

    private func endStream(_ request: HTTPRequest) async throws -> HTTPResponse {
        let id = try streamID(request)
        try await guardGeneration(id)
        do {
            let result = try await worker.endSessionAndWait(id: id)
            await sessions.markEnded(id)
            await idle.streamClosed(id)
            await worker.cleanup()
            return .json(["stream": id, "ended": true, "result": result])
        } catch {
            // A failed end must still release the stream slot; otherwise a dead worker keeps
            // its model "in use" forever and idle unload can never run.
            await sessions.markEnded(id)
            await idle.streamClosed(id)
            throw error
        }
    }

    private func cancelStream(_ request: HTTPRequest) async throws -> HTTPResponse {
        let id = try streamID(request)
        try await guardGeneration(id)
        do {
            try await worker.cancelSession(id: id)
        } catch {
            // The worker may have restarted; the session is dead either way.
        }
        await worker.forgetSession(id)
        await sessions.remove(id)
        await idle.streamClosed(id)
        return .json(["stream": id, "cancelled": true])
    }

    private func streamEvents(_ request: HTTPRequest) async throws -> HTTPResponse {
        let id = try streamID(request)
        let since = Int(request.query["since"] ?? "0") ?? 0
        let timeout = min(Double(request.query["wait"] ?? "0") ?? 0, 25)
        let (events, next) = await worker.pollEvents(session: id, since: since, timeout: timeout)
        let ended = await sessions.isEnded(id) || (events.contains { $0.name == "ended" })
        if ended {
            await sessions.markEnded(id)
            await idle.streamClosed(id)
        }
        for reaped in await sessions.sweep(now: Date()) { await idle.streamClosed(reaped) }
        await worker.cleanup()
        return .json([
            "stream": id,
            "next": next,
            "ended": ended,
            "events": events.map { ["event": $0.name, "data": $0.data] },
        ])
    }

    private func guardGeneration(_ id: String) async throws {
        let sessionGeneration = await sessions.generation(of: id)
        let workerGeneration = await worker.generation
        guard sessionGeneration == workerGeneration else {
            throw RuntimeError.message("worker restarted; reopen stream")
        }
    }

    private func streamID(_ request: HTTPRequest) throws -> String {
        if let id = request.query["stream"] { return id }
        if let id = (try? Self.body(request))?["stream"] as? String { return id }
        throw RuntimeError.message("query parameter \"stream\" is required")
    }

    static func body(_ request: HTTPRequest) throws -> [String: Any] {
        guard !request.body.isEmpty else { return [:] }
        guard let object = try? JSONSerialization.jsonObject(with: request.body) as? [String: Any] else {
            throw RuntimeError.message("request body must be a JSON object")
        }
        return object
    }
}

private actor SessionTable {
    struct Entry {
        var model: String
        var samples: Int
        var ended: Bool
        var endedAt: Date?
        /// Worker generation the session was opened against; mismatch means the worker died.
        var generation: Int
    }

    /// Ended/cancelled sessions live this much longer so late event polls can still read them.
    static let endedGrace: TimeInterval = 300

    private var entries: [String: Entry] = [:]

    func insert(_ id: String, model: String, config: [String: Any], generation: Int) {
        entries[id] = Entry(model: model, samples: 0, ended: false, endedAt: nil, generation: generation)
    }

    func generation(of id: String) -> Int? {
        entries[id]?.generation
    }

    func addedSamples(_ id: String, count: Int) -> Int {
        guard var entry = entries[id] else { return count }
        entry.samples += count
        entries[id] = entry
        return entry.samples
   }

    func markEnded(_ id: String) {
        guard entries[id] != nil else { return }
        entries[id]!.ended = true
        entries[id]!.endedAt = Date()
    }

    func isEnded(_ id: String) -> Bool {
        entries[id]?.ended ?? true
   }

    func remove(_ id: String) {
        entries.removeValue(forKey: id)
    }

    func count() -> Int {
        entries.count
    }

    func activeCount() -> Int {
        entries.values.filter { !$0.ended }.count
    }

    /// Drops ended sessions retained past the grace period so health counts and memory fall.
    /// Returns the removed ids so the caller can release their idle-policy slots.
    func sweep(now: Date = Date(), grace: TimeInterval = SessionTable.endedGrace) -> [String] {
        var removed: [String] = []
        entries = entries.filter { entry in
            guard entry.value.ended, let endedAt = entry.value.endedAt else { return true }
            let keep = now.timeIntervalSince(endedAt) < grace
            if !keep { removed.append(entry.key) }
            return keep
        }
        return removed
    }
}
