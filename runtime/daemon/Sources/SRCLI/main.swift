import Foundation
import SpeechRuntimeCore

/// `sr`: command line client of a running Speech Runtime. Uses only the public loopback API.
@main
struct CLI {
    static func main() async {
        var arguments = Array(CommandLine.arguments.dropFirst())
        guard let command = arguments.first else {
            usage()
            exit(2)
        }
        arguments.removeFirst()
        do {
            switch command {
            case "health": try await health()
            case "models": try await models()
            case "load": try await load(arguments)
            case "unload": try await unload(arguments)
            case "transcribe": try await transcribe(arguments)
            case "stream": try await stream(arguments)
            case "help", "--help", "-h": usage()
            default:
                FileHandle.standardError.write(Data("unknown command \(command)\n".utf8))
                usage()
                exit(2)
            }
        } catch {
            FileHandle.standardError.write(Data("sr: \(error)\n".utf8))
            exit(1)
        }
    }

    static func usage() {
        print("""
        sr <command>

          health                         worker and stream state
          models                         configured models and what is loaded
          load <model> [--backend auto|gpu|ane]
          unload <model>
          transcribe <file.wav> --model <name> [--language ru] [--window-ms N] [--priority batch|interactive]
          stream <file.wav> --model <name> [--realtime N] [--language ru] [--json]
                                         feed a file as a live stream; prints finals with latencies
        """)
    }

    static func client() throws -> RuntimeClient {
        var client = try RuntimeClient.local()
        if let base = ProcessInfo.processInfo.environment["SPEECH_RUNTIME_URL"], let url = URL(string: base) {
            client.baseURL = url
        }
        return client
    }

    static func option(_ name: String, in arguments: [String]) -> String? {
        guard let index = arguments.firstIndex(of: name), index + 1 < arguments.count else { return nil }
        return arguments[index + 1]
    }

    static func printJSON(_ value: [String: Any]) {
        let data = try? JSONSerialization.data(withJSONObject: value, options: [.prettyPrinted, .withoutEscapingSlashes])
        print(String(data: data ?? Data(), encoding: .utf8) ?? "{}")
    }

    static func health() async throws {
        let result = try await client().health()
        let worker = result["worker"] as? [String: Any] ?? [:]
        let models = (worker["models"] as? [[String: Any]] ?? []).filter { ($0["loaded"] as? Bool) == true }
        print("runtime: ok, uptime \(result["uptime_s"] ?? 0) s, streams \(result["streams"] ?? 0)")
        print("worker: pid \(worker["pid"] ?? -1), restarts \(worker["restarts"] ?? 0)")
        if models.isEmpty {
            print("loaded models: none")
        } else {
            for model in models {
                print("loaded: \(model["model"] ?? "?") on \(model["device"] ?? "?")")
            }
        }
    }

    static func models() async throws {
        let result = try await client().models()
        let rows = result["models"] as? [[String: Any]] ?? []
        for row in rows {
            let loaded = (row["loaded"] as? Bool) == true ? "loaded" : "idle"
            let device = row["device"] as? String ?? "-"
            print("\(row["model"] ?? "?")  \(loaded)  backend=\(row["backend"] ?? "auto")  device=\(device)")
            print("  \(row["path"] ?? "")")
        }
    }

    static func load(_ arguments: [String]) async throws {
        guard let model = arguments.first, !model.hasPrefix("--") else {
            throw RuntimeError.message("usage: sr load <model> [--backend auto|gpu|ane]")
        }
        let backend = option("--backend", in: arguments)
        let started = Date()
        let result = try await client().load(model: model, backend: backend)
        let inner = result["result"] as? [String: Any] ?? [:]
        let ms = Int(Date().timeIntervalSince(started) * 1000)
        print("loaded \(model) on \(inner["device"] ?? "?") in \(ms) ms (worker load \(inner["load_ms"] ?? "?") ms)")
    }

    static func unload(_ arguments: [String]) async throws {
        guard let model = arguments.first, !model.hasPrefix("--") else {
            throw RuntimeError.message("usage: sr unload <model>")
        }
        _ = try await client().unload(model: model)
        print("unloaded \(model)")
    }

    static func transcribe(_ arguments: [String]) async throws {
        guard let path = arguments.first, !path.hasPrefix("--") else {
            throw RuntimeError.message("usage: sr transcribe <file.wav> --model <name>")
        }
        guard let model = option("--model", in: arguments) else {
            throw RuntimeError.message("--model is required")
        }
        let wav = try Data(contentsOf: URL(fileURLWithPath: path))
        let started = Date()
        let result = try await client().transcribe(
            wav: wav,
            model: model,
            language: option("--language", in: arguments),
            windowMs: option("--window-ms", in: arguments).flatMap(Int.init),
            priority: option("--priority", in: arguments) ?? "batch"
        )
        let wall = Int(Date().timeIntervalSince(started) * 1000)
        let audioMs = result["audio_ms"] as? Int ?? 0
        let asrMs = result["asr_ms"] as? Int ?? 0
        print("audio \(audioMs) ms, asr \(asrMs) ms, wall \(wall) ms, rtf \(String(format: "%.3f", Double(asrMs) / Double(max(audioMs, 1))))")
        print(result["text"] as? String ?? "")
    }

    /// Feeds the file at `--realtime` speed (default 1.0) in 100 ms chunks, polling events between
    /// chunks. This is the same path a microphone client uses, so the printed latency is real.
    static func stream(_ arguments: [String]) async throws {
        guard let path = arguments.first, !path.hasPrefix("--") else {
            throw RuntimeError.message("usage: sr stream <file.wav> --model <name> [--realtime N]")
        }
        guard let model = option("--model", in: arguments) else {
            throw RuntimeError.message("--model is required")
        }
        let json = arguments.contains("--json")
        let speed = Double(option("--realtime", in: arguments) ?? "1") ?? 1
        let wav = try Data(contentsOf: URL(fileURLWithPath: path))
        let (samples, rate) = try LocalPCM.decodeWAV(wav)
        guard abs(rate - 16_000) < 1 else {
            throw RuntimeError.message("file is \(Int(rate)) Hz; convert to 16000 Hz mono first")
        }

        let client = try client()
        var config: [String: Any] = [:]
        if let language = option("--language", in: arguments) { config["language"] = language }
        let opened = try await client.openStream(model: model, config: config)
        guard let stream = opened["stream"] as? String else {
            throw RuntimeError.message("daemon did not return a stream id")
        }

        let chunk = 1600
        let started = Date()
        var nextCursor = 0
        var finals = 0
        var markdownLines: [String] = []
        /// Audio position (s) that the client has fed, paired with the wall clock at feed time.
        var fedAudio = 0.0
        var fedClock = Date()

        func wallClock(atAudio seconds: Double) -> Date {
            // Feeding is paced, so a position between checkpoints maps linearly onto the clock.
            let behind = max(fedAudio - seconds, 0)
            return fedClock.addingTimeInterval(-behind / speed)
        }

        func drain(wait: Double) async throws {
            let batch = try await client.events(stream: stream, since: nextCursor, wait: wait)
            nextCursor = batch["next"] as? Int ?? nextCursor
            for event in batch["events"] as? [[String: Any]] ?? [] {
                guard let name = event["event"] as? String else { continue }
                let data = event["data"] as? [String: Any] ?? [:]
                if name == "final" || name == "error" {
                    let at = Date().timeIntervalSince(started)
                    if name == "final" {
                        finals += 1
                        let lastVoice = Double(data["last_voice_ms"] as? Int ?? 0) / 1000
                        let text = data["text"] as? String ?? ""
                        let asrMs = data["asr_ms"] as? Int ?? 0
                        let latency = Date().timeIntervalSince(wallClock(atAudio: lastVoice))
                        if json {
                            markdownLines.append(
                                "{\"utterance\":\(data["utterance"] ?? 0),\"at_s\":\(String(format: "%.2f", at)),\"latency_ms\":\(Int(latency * 1000)),\"asr_ms\":\(asrMs),\"text\":\(jsonString(text))}"
                            )
                        } else {
                            print(String(format: "#%d at %.2f s, endpoint→final %.0f ms, asr %d ms",
                                         data["utterance"] as? Int ?? finals, at, latency * 1000, asrMs))
                            print("   \(text)")
                        }
                    } else {
                        let message = data["message"] as? String ?? "error"
                        FileHandle.standardError.write(Data("stream error: \(message)\n".utf8))
                    }
                }
            }
        }

        var offset = 0
        while offset < samples.count {
            let end = min(offset + chunk, samples.count)
            _ = try await client.streamAudio(stream: stream, pcm: Array(samples[offset..<end]))
            offset = end
            fedAudio = Double(offset) / 16_000
            fedClock = Date()
            try await drain(wait: 0)
            // Pace the feed so the model sees roughly real time; polling stays responsive.
            let target = Double(offset) / 16_000 / speed
            let elapsed = Date().timeIntervalSince(started)
            if target > elapsed {
                try await Task.sleep(nanoseconds: UInt64((target - elapsed) * 1_000_000_000))
            }
        }
        _ = try await client.endStream(stream)
        for _ in 0..<200 {
            try await drain(wait: 0.1)
            let batch = try await client.events(stream: stream, since: nextCursor, wait: 0.05)
            nextCursor = batch["next"] as? Int ?? nextCursor
            if finals > 0, (batch["events"] as? [[String: Any]] ?? []).isEmpty, (batch["ended"] as? Bool) == true {
                break
            }
        }
        _ = try await client.cancelStream(stream)
        let wall = Date().timeIntervalSince(started)
        if json {
            print("[" + markdownLines.joined(separator: ",\n") + "]")
        }
        FileHandle.standardError.write(Data(String(
            format: "fed %.1f s of audio in %.1f s, %d utterance(s)\n",
            Double(samples.count) / 16_000, wall, finals
        ).utf8))
    }

    static func jsonString(_ text: String) -> String {
        let data = try? JSONSerialization.data(withJSONObject: [text])
        let encoded = String(data: data ?? Data("[]".utf8), encoding: .utf8) ?? "[]"
        return String(encoded.dropFirst().dropLast())
    }
}
