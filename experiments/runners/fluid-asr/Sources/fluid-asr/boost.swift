import AVFoundation
import FluidAudio
import Foundation

// Parakeet TDT v3 через скользящее окно: A/B для vocabulary boosting и замеры
// задержки стриминга.
//
//   fluid-asr parakeet-boost --files list.txt --out-dir DIR [--vocab words.txt] [--language ru] [--realtime]
//
// Окно по умолчанию 11+2+2 с — штатная раскладка движка, а не настройка под
// корпус. С `--realtime` порции подаются по часам; без него — как можно быстрее.
// Менеджер создаётся на каждый файл: `finish()` закрывает входной AsyncStream,
// и повторный запуск на том же объекте молча даёт пустой текст.

final class UpdateCollector: @unchecked Sendable {
    private let lock = NSLock()
    private var storage: [(at: Double, text: String, confirmed: Bool, confidence: Double)] = []

    func append(at: Double, text: String, confirmed: Bool, confidence: Double) {
        lock.lock()
        storage.append((at, text, confirmed, confidence))
        lock.unlock()
    }

    func snapshot() -> [(at: Double, text: String, confirmed: Bool, confidence: Double)] {
        lock.lock()
        defer { lock.unlock() }
        return storage
    }
}

func runParakeetBoost(_ args: Args) async {
    guard let listPath = args.value("files"), let outDir = args.value("out-dir") else {
        fail("нужны --files и --out-dir")
    }
    let files = readList(listPath)
    let languageCode = args.value("language")
    let useVocabulary = args.value("vocab")
    let realtime = args.flag("realtime")
    let stepMs = Int(args.value("step-ms") ?? "20") ?? 20
    try? FileManager.default.createDirectory(atPath: outDir, withIntermediateDirectories: true)

    let loadStart = timestamp()
    let models: AsrModels
    do {
        models = try await AsrModels.downloadAndLoad(version: .v3)
    } catch {
        fail("не загрузились модели Parakeet v3: \(error)")
    }
    var ctcModels: CtcModels? = nil
    if useVocabulary != nil {
        do {
            let ctcDir = URL(fileURLWithPath: NSHomeDirectory())
                .appendingPathComponent("Library/Application Support/FluidAudio/Models/parakeet-ctc-110m-coreml")
            ctcModels = try await CtcModels.load(from: ctcDir, variant: .ctc110m)
        } catch {
            fail("не загрузились CTC-модели: \(error)")
        }
    }

    var terms: [CustomVocabularyTerm] = []
    if let vocabPath = useVocabulary {
        let words = (try? String(contentsOfFile: vocabPath, encoding: .utf8))?
            .split(whereSeparator: \.isNewline)
            .map { $0.trimmingCharacters(in: .whitespaces) }
            .filter { !$0.isEmpty } ?? []
        terms = words.map { CustomVocabularyTerm(text: $0) }
    }

    let language: Language? = languageCode.map { $0 == "ru" ? .russian : .english }
    // Окно задаётся явно: движок требует, чтобы left+chunk+right помещалось в
    // 15 с входа модели, поэтому шаг выдачи меняется отдельно от окна.
    let base = SlidingWindowAsrConfig.streaming
    let config = SlidingWindowAsrConfig(
        chunkSeconds: Double(args.value("chunk-sec") ?? "") ?? base.chunkSeconds,
        hypothesisChunkSeconds: Double(args.value("hypothesis-sec") ?? "") ?? base.hypothesisChunkSeconds,
        leftContextSeconds: base.leftContextSeconds,
        rightContextSeconds: base.rightContextSeconds,
        minContextForConfirmation: base.minContextForConfirmation,
        confirmationThreshold: base.confirmationThreshold,
        tdtConfig: nil,
        language: language
    )
    let loadSeconds = timestamp() - loadStart

    var rows: [String] = []
    var timings: [[String: Any]] = []
    var audioTotal = 0.0
    var inferenceTotal = 0.0

    for file in files {
        let id = file.deletingPathExtension().lastPathComponent
        let samples = try! loadPcm16k(file)
        let duration = Double(samples.count) / 16_000.0
        let manager = SlidingWindowAsrManager(config: config)
        do {
            try await manager.loadModels(models)
            if let ctcModels, !terms.isEmpty {
                try await manager.configureVocabularyBoosting(
                    vocabulary: CustomVocabularyContext(terms: terms), ctcModels: ctcModels)
            }
        } catch {
            fail("не инициализировался менеджер на \(id): \(error)")
        }

        let collector = UpdateCollector()
        let started = timestamp()
        let consumer = Task {
            for await update in await manager.transcriptionUpdates {
                collector.append(at: timestamp() - started, text: update.text,
                                 confirmed: update.isConfirmed, confidence: Double(update.confidence))
            }
        }
        do {
            try await manager.startStreaming(source: .microphone)
            let step = max(1, 16_000 * stepMs / 1000)
            var offset = 0
            while offset < samples.count {
                let end = min(offset + step, samples.count)
                let chunk = Array(samples[offset..<end])
                guard let format = AVAudioFormat(commonFormat: .pcmFormatFloat32, sampleRate: 16_000,
                                                 channels: 1, interleaved: false),
                      let buffer = AVAudioPCMBuffer(pcmFormat: format, frameCapacity: AVAudioFrameCount(chunk.count))
                else { fail("не создаётся буфер") }
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
            let feedDone = timestamp()
            let text = try await manager.finish()
            let finished = timestamp()
            consumer.cancel()

            let updates = collector.snapshot()
            let first = updates.first { !$0.text.trimmingCharacters(in: .whitespaces).isEmpty }
            // Пересмотры: подтверждённый текст изменился после того, как был подтверждён.
            var confirmedText = ""
            var revisions = 0
            for update in updates where update.confirmed {
                let current = update.text.trimmingCharacters(in: .whitespaces)
                if !confirmedText.isEmpty && !current.hasPrefix(confirmedText) && !confirmedText.hasPrefix(current) {
                    revisions += 1
                }
                confirmedText = current
            }

            rows.append("\(id)\t\(text.replacingOccurrences(of: "\t", with: " "))")
            audioTotal += duration
            inferenceTotal += finished - started
            timings.append([
                "segment_id": id,
                "audio_s": duration,
                "wall_s": finished - started,
                "rtf": duration > 0 ? (finished - started) / duration : 0,
                "first_partial_s": first?.at as Any,
                "feed_s": feedDone - started,
                "final_tail_s": finished - feedDone,
                "updates": updates.count,
                "confirmed_updates": updates.filter(\.confirmed).count,
                "confirmed_revisions": revisions,
            ])
        } catch {
            consumer.cancel()
            fail("ошибка на \(id): \(error)")
        }
        FileHandle.standardError.write("done \(id)\n".data(using: .utf8)!)
    }

    let suffix = terms.isEmpty ? "plain" : "vocab"
    writeFile("\(outDir)/parakeet-\(suffix).tsv", rows.joined(separator: "\n") + "\n")
    writeFile("\(outDir)/parakeet-\(suffix).json", jsonString([
        "status": "streaming_measurement_not_benchmark",
        "model": "parakeet-tdt-0.6b-v3",
        "engine": "SlidingWindowAsrManager, 11+2+2 s window" + (terms.isEmpty ? "" : ", CTC-110m rescorer"),
        "language": languageCode ?? "auto",
        "realtime": realtime,
        "step_ms": stepMs,
        "chunk_s": config.chunkSeconds,
        "hypothesis_s": config.hypothesisChunkSeconds,
        "vocab_terms": terms.count,
        "load_s": loadSeconds,
        "audio_s": audioTotal,
        "inference_s": inferenceTotal,
        "rtf": audioTotal > 0 ? inferenceTotal / audioTotal : 0,
        "segments": timings,
    ]))
}
