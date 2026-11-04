import Foundation
import SpeechRuntimeCore

/// Serialized append-only log: several tasks log at once, and unsynchronized
/// seek+write pairs interleave partial lines.
final class LogSink: @unchecked Sendable {
    private let lock = NSLock()
    private let handle: FileHandle?
    private let formatter = ISO8601DateFormatter()

    init(url: URL) {
        if !FileManager.default.fileExists(atPath: url.path) {
            FileManager.default.createFile(atPath: url.path, contents: nil)
        }
        handle = try? FileHandle(forWritingTo: url)
    }

    func log(_ line: String) {
        let text = "\(formatter.string(from: Date())) \(line)\n"
        lock.lock()
        defer { lock.unlock() }
        FileHandle.standardError.write(Data(text.utf8))
        guard let handle else { return }
        _ = try? handle.seekToEnd()
        try? handle.write(contentsOf: Data(text.utf8))
    }
}

/// `speech-runtime`: loopback HTTP daemon in front of one `sr-worker` child.
///
/// Flags: --port, --config, --token-file, --worker, --idle-unload <seconds>.
@main
struct Daemon {
    static func main() async {
        var port: UInt16 = 8477
        var configPath = Paths.config.path
        var tokenPath = Paths.token.path
        var workerPath = Paths.workerBinary()
        var idleSeconds = 900

        var arguments = Array(CommandLine.arguments.dropFirst()).makeIterator()
        while let argument = arguments.next() {
            switch argument {
            case "--port": port = UInt16(arguments.next() ?? "") ?? port
            case "--config": configPath = arguments.next() ?? configPath
            case "--token-file": tokenPath = arguments.next() ?? tokenPath
            case "--worker": workerPath = arguments.next() ?? workerPath
            case "--idle-unload": idleSeconds = Int(arguments.next() ?? "") ?? idleSeconds
            case "--help", "-h":
                print("""
                speech-runtime [--port 8477] [--config <path>] [--token-file <path>] [--worker <path>]
                               [--idle-unload <seconds>]

                Config format: {"port": 8477, "idleUnloadSeconds": 900,
                                "models": {"gigaam": {"path": "/abs/model.gguf", "backend": "ane"}}}
                """)
                exit(0)
            default:
                FileHandle.standardError.write(Data("unknown argument \(argument)\n".utf8))
                exit(2)
            }
        }

        try? FileManager.default.createDirectory(at: Paths.support, withIntermediateDirectories: true)
        let sink = LogSink(url: Paths.log)
        let logger: @Sendable (String) -> Void = { sink.log($0) }

        do {
            var config = try RuntimeConfig.load(at: URL(fileURLWithPath: configPath))
            if let flag = ProcessInfo.processInfo.environment["SPEECH_RUNTIME_PORT"], let value = UInt16(flag) {
                port = value
            } else if port == 8477 {
                port = config.port
            }
            config.port = port

            let token = try AuthToken.loadOrCreate(at: URL(fileURLWithPath: tokenPath))
            let worker = WorkerClient(workerPath: workerPath, log: logger)
            try await worker.start()
            logger("worker started pid=\(await worker.pid ?? -1) binary=\(workerPath)")

            let idle = IdlePolicy()
            let snapshot = config
            let router = Router(token: token, worker: worker, config: { snapshot }, idle: idle, log: logger)
            let server = try HTTPServer(port: port) { request in
                await router.handle(request)
            }
            server.start()
            logger("listening on http://127.0.0.1:\(port) token-file=\(tokenPath)")

            let monitor = Task {
                var lastGeneration = await worker.generation
                while !Task.isCancelled {
                    try? await Task.sleep(nanoseconds: 30 * 1_000_000_000)
                    let generation = await worker.generation
                    if generation != lastGeneration {
                        await idle.pruneStreams(before: generation)
                        lastGeneration = generation
                    }
                    let stats = try? await worker.stats()
                    let loaded = (stats?["models"] as? [[String: Any]] ?? []).filter {
                        ($0["loaded"] as? Bool) == true
                    }.compactMap { $0["model"] as? String }
                    let idleModels = await idle.modelsToUnload(idle: TimeInterval(idleSeconds)) { model in
                        loaded.contains(model)
                    }
                    for model in idleModels {
                        do {
                            _ = try await worker.unload(model: model)
                            await idle.didUnload(model)
                            logger("unloaded idle model \(model) after \(idleSeconds) s")
                        } catch {
                            // "in use" means a session or queued/running job still needs the model:
                            // keep tracking it and retry after the idle window elapses again.
                            await idle.touch(model)
                            logger("idle unload of \(model) failed, will retry: \(error)")
                        }
                    }
                }
            }

            signal(SIGINT, SIG_IGN)
            signal(SIGTERM, SIG_IGN)
            let done = DispatchSemaphore(value: 0)
            signalSources = [SIGINT, SIGTERM].map { sig in
                let source = DispatchSource.makeSignalSource(signal: sig, queue: .main)
                source.setEventHandler { done.signal() }
                source.resume()
                return source
            }
            await withCheckedContinuation { (continuation: CheckedContinuation<Void, Never>) in
                DispatchQueue.global().async {
                    done.wait()
                    continuation.resume()
                }
            }
            monitor.cancel()
            server.stop()
            await worker.stop()
            logger("stopped")
        } catch {
            logger("fatal: \(error)")
            FileHandle.standardError.write(Data("speech-runtime: \(error)\n".utf8))
            exit(1)
        }
    }
}

private var signalSources: [any DispatchSourceSignal] = []
