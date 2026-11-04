import Foundation

/// Supervises `sr-worker`: one child process that owns the models, the per-model queues and the
/// dictation endpointer. The daemon never talks to wavo directly, so a worker crash cannot take
/// the API down; the supervisor restarts it and reports the failure to the client that asked.
public actor WorkerClient {
    public struct SessionEvent: @unchecked Sendable {
        public var name: String
        public var data: [String: Any]
    }

    private var process: Process?
    private var stdinPipe: Pipe?
    private var reader: Task<Void, Never>?
    private var stderrReader: Task<Void, Never>?
    private nonisolated let pending = PendingReplies()
    private var nextID: UInt64 = 1
    /// Ops whose traffic is per-audio-chunk or periodic: logging them buries real events.
    private static let highFrequency: Set<String> = ["session_audio", "stats"]
    private var opNames: [UInt64: String] = [:]
    private var sessionBuffers: [String: [SessionEvent]] = [:]
    private var sessionWaiters: [CheckedContinuation<Void, Never>] = []
    private let workerPath: String
    private let log: @Sendable (String) -> Void
    private var restarts = 0
    private var lastExit: Int32?
    private var startedAt = Date()

    public init(workerPath: String, log: @escaping @Sendable (String) -> Void) {
        self.workerPath = workerPath
        self.log = log
    }

    public var pid: Int32? { process?.processIdentifier }
    public var restartCount: Int { restarts }
    public var workerLastExit: Int32? { lastExit }
    public var uptime: TimeInterval { Date().timeIntervalSince(startedAt) }

    public func start() throws {
        guard process == nil else { return }
        let child = Process()
        child.executableURL = URL(fileURLWithPath: workerPath)
        let inPipe = Pipe()
        let outPipe = Pipe()
        let errPipe = Pipe()
        child.standardInput = inPipe
        child.standardOutput = outPipe
        child.standardError = errPipe
        try child.run()
        process = child
        stdinPipe = inPipe
        startedAt = Date()

        // `FileHandle.bytes.lines` can hold a complete line for tens of seconds on a pipe, which
        // turns into spurious request timeouts. Read the pipe on its own thread and hand ordered
        // lines to the actor through an unbounded stream.
        let (lines, continuation) = AsyncStream<String>.makeStream(bufferingPolicy: .unbounded)
        let stdoutHandle = outPipe.fileHandleForReading
        let thread = Thread {
            var buffer = Data()
            while true {
                let chunk = stdoutHandle.availableData
                if chunk.isEmpty { break }
                buffer.append(chunk)
                while let newline = buffer.firstIndex(of: 0x0A) {
                    let line = String(decoding: buffer[..<newline], as: UTF8.self)
                    buffer.removeSubrange(buffer.startIndex...newline)
                    continuation.yield(line)
                }
            }
            continuation.finish()
        }
        thread.stackSize = 1 << 20
        thread.start()
        reader = Task { [weak self] in
            for await line in lines {
                guard let self else { return }
                await self.handle(line: line)
            }
            await self?.workerExited()
        }
        stderrReader = Task { [weak self] in
            guard let self else { return }
            do {
                for try await line in errPipe.fileHandleForReading.bytes.lines {
                    await self.log("worker: \(line)")
                }
            } catch {}
        }
    }

    public func stop() {
        reader?.cancel()
        stderrReader?.cancel()
        if let process, process.isRunning {
            process.terminate()
        }
        process = nil
        stdinPipe = nil
        failPending("worker stopped")
    }

    // MARK: - Protocol

    public func request(_ payload: [String: Any], timeout: TimeInterval) async throws -> [String: Any] {
        guard let stdinPipe, process?.isRunning == true else {
            throw RuntimeError.message("worker is not running")
        }
        let id = nextID
        nextID += 1
        var payload = payload
        payload["id"] = id
        let data = try JSONSerialization.data(withJSONObject: payload)
        guard var line = String(data: data, encoding: .utf8) else {
            throw RuntimeError.message("cannot encode request")
        }
        line += "\n"
        let bytes = Data(line.utf8)

        let op = payload["op"] as? String ?? "?"
        let verbose = !Self.highFrequency.contains(op)
        opNames[id] = op
        if verbose { log("→ [\(id)] \(op) \(bytes.count) B") }
        return try await withCheckedThrowingContinuation { continuation in
            pending.insert(id, continuation)
            stdinPipe.fileHandleForWriting.write(bytes)
            Task { [pending] in
                try? await Task.sleep(nanoseconds: UInt64(timeout * 1_000_000_000))
                pending.take(id)?.resume(throwing: RuntimeError.message("worker request timed out after \(Int(timeout)) s"))
            }
        }
    }

    public func ping() async throws -> [String: Any] {
        try await request(["op": "ping"], timeout: 5)
    }

    public func stats() async throws -> [String: Any] {
        try await request(["op": "stats"], timeout: 5)
    }

    public func load(model: String, path: String, backend: String? = nil) async throws -> [String: Any] {
        var payload: [String: Any] = ["op": "load", "model": model, "path": path]
        if let backend { payload["backend"] = backend }
        return try await request(payload, timeout: 180)
    }

    public func unload(model: String) async throws -> [String: Any] {
        try await request(["op": "unload", "model": model], timeout: 30)
    }

    public func transcribe(
        pcm: [Float],
        model: String,
        language: String?,
        windowMs: Int?,
        priority: String
    ) async throws -> [String: Any] {
        var payload: [String: Any] = [
            "op": "transcribe",
            "model": model,
            "pcm": Self.encode(pcm),
            "priority": priority,
        ]
        if let language { payload["language"] = language }
        if let windowMs { payload["window_ms"] = windowMs }
        return try await request(payload, timeout: 300)
    }

    public func openSession(id: String, model: String, config: [String: Any]) async throws -> [String: Any] {
        try await request(["op": "session_open", "session": id, "model": model, "config": config], timeout: 10)
    }

    public func sessionAudio(id: String, pcm: [Float]) async throws {
        _ = try await request(["op": "session_audio", "session": id, "pcm": Self.encode(pcm)], timeout: 30)
    }

    public func endSession(id: String) async throws -> [String: Any] {
        try await request(["op": "session_end", "session": id], timeout: 10)
    }

    public func cancelSession(id: String) async throws {
        _ = try await request(["op": "session_cancel", "session": id], timeout: 10)
    }

    /// Returns buffered events after `since`. Waits up to `timeout` for the first one.
    public func pollEvents(session: String, since: Int, timeout: TimeInterval) async -> (events: [SessionEvent], next: Int) {
        let deadline = Date().addingTimeInterval(timeout)
        while true {
            let buffered = sessionBuffers[session] ?? []
            if buffered.count > since {
                return (Array(buffered[since...]), buffered.count)
            }
            if timeout <= 0 { return ([], since) }
            let remaining = deadline.timeIntervalSinceNow
            if remaining <= 0 { return ([], since) }
            await withCheckedContinuation { (continuation: CheckedContinuation<Void, Never>) in
                sessionWaiters.append(continuation)
                Task {
                    try? await Task.sleep(nanoseconds: UInt64(max(remaining, 0.001) * 1_000_000_000))
                    await self.wakeWaiters()
                }
            }
        }
    }

    private func wakeWaiters() {
        let waiters = sessionWaiters
        sessionWaiters.removeAll()
        for waiter in waiters { waiter.resume() }
    }

    private func handle(line: String) async {
        guard let data = line.data(using: .utf8),
              let object = try? JSONSerialization.jsonObject(with: data) as? [String: Any],
              let type = object["type"] as? String
        else {
            log("worker sent unparsable line: \(line.prefix(200))")
            return
        }

        switch type {
        case "ok", "error":
            guard let id = (object["id"] as? NSNumber)?.uint64Value,
                  let continuation = pending.take(id)
            else {
                log("worker reply without waiter: \(line.prefix(200))")
                return
            }
            if let op = opNames.removeValue(forKey: id), !Self.highFrequency.contains(op) {
                log("← [\(id)] \(type)")
            }
            if type == "error" {
                continuation.resume(throwing: RuntimeError.message(object["message"] as? String ?? "worker error"))
            } else {
                continuation.resume(returning: object["result"] as? [String: Any] ?? [:])
            }
        case "event":
            guard let session = object["session"] as? String,
                  let name = object["event"] as? String
            else { return }
            var payload = object
            payload.removeValue(forKey: "type")
            payload.removeValue(forKey: "session")
            payload.removeValue(forKey: "event")
            sessionBuffers[session, default: []].append(SessionEvent(name: name, data: payload))
            wakeWaiters()
        default:
            log("worker sent unknown message type: \(type)")
        }
    }

    private func workerExited() {
        let code = process?.terminationStatus ?? -1
        lastExit = code
        process = nil
        stdinPipe = nil
        failPending("worker exited with status \(code)")
        for session in sessionBuffers.keys {
            sessionBuffers[session, default: []].append(
                SessionEvent(name: "error", data: ["message": "worker exited with status \(code)"])
            )
        }
        wakeWaiters()
        log("worker exited with status \(code)")
        restarts += 1
        do {
            try start()
            log("worker restarted")
        } catch {
            log("worker restart failed: \(error)")
        }
    }

    private func failPending(_ message: String) {
        pending.failAll(message)
    }

    public static func encode(_ pcm: [Float]) -> String {
        var data = Data(capacity: pcm.count * 4)
        for sample in pcm {
            var bits = sample.bitPattern.littleEndian
            withUnsafeBytes(of: &bits) { data.append(contentsOf: $0) }
        }
        return data.base64EncodedString()
    }
}

/// Replies are matched on the caller's thread so a reply can never race the registration of the
/// continuation that is waiting for it.
final class PendingReplies: @unchecked Sendable {
    private let lock = NSLock()
    private var map: [UInt64: CheckedContinuation<[String: Any], Error>] = [:]

    func insert(_ id: UInt64, _ continuation: CheckedContinuation<[String: Any], Error>) {
        lock.lock()
        map[id] = continuation
        lock.unlock()
    }

    func take(_ id: UInt64) -> CheckedContinuation<[String: Any], Error>? {
        lock.lock()
        defer { lock.unlock() }
        return map.removeValue(forKey: id)
    }

    func failAll(_ message: String) {
        lock.lock()
        let waiting = map
        map.removeAll()
        lock.unlock()
        for (_, continuation) in waiting {
            continuation.resume(throwing: RuntimeError.message(message))
        }
    }
}
