import AVFoundation
import FluidAudio
import Foundation

/// Подаёт готовый WAV порциями по часам через `SlidingWindowAsrManager` (Parakeet
/// TDT/Ultra) и пишет каждое обновление текста с моментом появления.
///
/// Окно собирается как left + chunk + right ≤ 15 с; `--hyp` задаёт шаг гипотезы.
/// Политика подтверждения (`--min-context`, `--threshold`) влияет только на
/// флаг `isConfirmed` и на пересмотры, но не на выдачу partial — поэтому
/// задержку первого partial определяют `--right` и `--hyp`.
func runStreamingParakeet(_ args: Args) async {
    guard let listPath = args.value("files"), let outJson = args.value("out-json") else {
        fail("нужны --files, --out-json")
    }
    let files = readList(listPath)
    let realtime = args.flag("realtime")
    let stepMs = Int(args.value("step-ms") ?? "20") ?? 20
    func seconds(_ name: String, _ fallback: Double) -> Double {
        guard let raw = args.value(name) else { return fallback }
        guard let value = Double(raw) else { fail("--\(name) ожидает число, получено \(raw)") }
        return value
    }
    let config = SlidingWindowAsrConfig(
        chunkSeconds: seconds("chunk", 11.0),
        hypothesisChunkSeconds: seconds("hyp", 2.0),
        leftContextSeconds: seconds("left", 2.0),
        rightContextSeconds: seconds("right", 2.0),
        minContextForConfirmation: seconds("min-context", 10.0),
        confirmationThreshold: seconds("threshold", 0.85),
        language: language(args.value("language"))
    )
    do {
        try config.validate()
    } catch {
        fail("недопустимая конфигурация окна: \(error)")
    }

    let loadStart = timestamp()
    let models: AsrModels
    do {
        models = try await AsrModels.downloadAndLoad(version: modelVersion(args.value("model")))
    } catch {
        fail("не загрузились модели Parakeet: \(error)")
    }
    let loadSeconds = timestamp() - loadStart

    let format = AVAudioFormat(standardFormatWithSampleRate: 16_000, channels: 1)!

    var reports: [[String: Any]] = []
    for file in files {
        let id = file.deletingPathExtension().lastPathComponent
        let manager = SlidingWindowAsrManager(config: config)
        final class Collector: @unchecked Sendable {
            private let lock = NSLock()
            private var storage: [[String: Any]] = []
            var startedAt = 0.0
            func append(_ update: SlidingWindowTranscriptionUpdate) {
                let at = Self.now() - startedAt
                let timings = update.tokenTimings.map {
                    ["text": $0.token, "start": Double($0.startTime), "end": Double($0.endTime)]
                }
                lock.lock()
                storage.append([
                    "at_s": at,
                    "text": update.text,
                    "is_confirmed": update.isConfirmed,
                    "confidence": Double(update.confidence),
                    "tokens": update.tokens,
                    "token_timings": timings,
                    "ctc_detected_terms": update.ctcDetectedTerms ?? [],
                    "ctc_applied_terms": update.ctcAppliedTerms ?? [],
                ])
                lock.unlock()
            }
            var snapshot: [[String: Any]] {
                lock.lock()
                defer { lock.unlock() }
                return storage
            }
            func reset(_ started: Double) {
                lock.lock()
                storage = []
                startedAt = started
                lock.unlock()
            }
            static func now() -> Double { Date().timeIntervalSince1970 }
        }
        let collector = Collector()

        do {
            try await manager.loadModels(models)
            let updates = await manager.transcriptionUpdates
            let consumer = Task {
                for await update in updates { collector.append(update) }
            }
            let samples = try loadPcm16k(file)
            let duration = Double(samples.count) / 16_000.0
            let step = max(1, 16_000 * stepMs / 1000)

            try await manager.startStreaming(source: .microphone)
            let started = timestamp()
            collector.reset(started)
            var offset = 0
            while offset < samples.count {
                let end = min(offset + step, samples.count)
                let chunk = Array(samples[offset..<end])
                let buffer = AVAudioPCMBuffer(pcmFormat: format, frameCapacity: AVAudioFrameCount(chunk.count))!
                buffer.frameLength = AVAudioFrameCount(chunk.count)
                chunk.withUnsafeBufferPointer { source in
                    buffer.floatChannelData![0].update(from: source.baseAddress!, count: chunk.count)
                }
                await manager.streamAudio(buffer)
                offset = end
                if realtime {
                    let target = started + Double(offset) / 16_000.0
                    let now = timestamp()
                    if target > now {
                        try? await Task.sleep(nanoseconds: UInt64((target - now) * 1_000_000_000))
                    }
                }
            }
            let finishStart = timestamp()
            let finalText = try await manager.finish()
            let finishSeconds = timestamp() - finishStart
            let wall = timestamp() - started
            consumer.cancel()
            let partials = collector.snapshot
            reports.append([
                "segment_id": id,
                "audio_s": duration,
                "wall_s": wall,
                "rtf": duration > 0 ? wall / duration : 0,
                "finish_s": finishSeconds,
                "final": finalText.replacingOccurrences(of: "\t", with: " "),
                "partials": partials,
                "partial_count": partials.count,
            ])
            await manager.cleanup()
        } catch {
            fail("ошибка на \(id): \(error)")
        }
        FileHandle.standardError.write("done \(id)\n".data(using: .utf8)!)
    }
    writeFile(outJson, jsonString([
        "status": "streaming_measurement_not_benchmark",
        "model": args.value("model") ?? "v3",
        "model_dir": args.value("model") ?? "v3",
        "engine": "FluidAudio SlidingWindowAsrManager",
        "config": [
            "chunk_s": config.chunkSeconds,
            "hypothesis_s": config.hypothesisChunkSeconds,
            "left_s": config.leftContextSeconds,
            "right_s": config.rightContextSeconds,
            "min_context_s": config.minContextForConfirmation,
            "threshold": config.confirmationThreshold,
        ],
        "language": args.value("language") ?? "auto",
        "realtime": realtime,
        "step_ms": stepMs,
        "load_s": loadSeconds,
        "segments": reports,
    ]))
}
