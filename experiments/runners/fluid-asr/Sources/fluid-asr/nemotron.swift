import FluidAudio
import Foundation

func runNemotronBatch(_ args: Args) async {
    guard let modelDir = args.value("model-dir"), let listPath = args.value("files"),
          let outTsv = args.value("out-tsv"), let outJson = args.value("out-json") else {
        fail("нужны --model-dir, --files, --out-tsv, --out-json")
    }
    let files = readList(listPath)
    let languageCode = args.value("language") ?? "ru-RU"
    let manager = StreamingNemotronMultilingualAsrManager()

    let loadStart = timestamp()
    do {
        try await manager.loadModels(from: URL(fileURLWithPath: modelDir, isDirectory: true))
    } catch {
        fail("не загрузились модели Nemotron из \(modelDir): \(error)")
    }
    let loadSeconds = timestamp() - loadStart

    var vocabTerms: [CustomVocabularyTerm] = []
    if let vocabPath = args.value("vocab") {
        let words = (try? String(contentsOfFile: vocabPath, encoding: .utf8))?
            .split(whereSeparator: \.isNewline)
            .map { $0.trimmingCharacters(in: .whitespaces) }
            .filter { !$0.isEmpty } ?? []
        vocabTerms = words.map { CustomVocabularyTerm(text: $0, weight: nil, aliases: nil, tokenIds: nil, ctcTokenIds: nil, minSimilarity: nil) }
        await manager.setCustomVocabulary(vocabTerms)
    }

    var tsv = ""
    var timings: [[String: Any]] = []
    var totalAudio = 0.0
    var totalInference = 0.0
    for file in files {
        let id = file.deletingPathExtension().lastPathComponent
        do {
            try await manager.reset()
            await manager.setLanguage(languageCode)
            let samples = try loadPcm16k(file)
            let start = timestamp()
            _ = try await manager.process(samples: samples)
            let text = try await manager.finish()
            let seconds = timestamp() - start
            let clean = text.replacingOccurrences(of: "\n", with: " ").replacingOccurrences(of: "\t", with: " ")
            tsv += "\(id)\t\(clean)\n"
            let duration = Double(samples.count) / 16_000.0
            totalAudio += duration
            totalInference += seconds
            timings.append([
                "segment_id": id, "inference_s": seconds, "audio_s": duration,
                "detected_language": await manager.detectedLanguage() ?? "",
            ])
        } catch {
            fail("ошибка на \(id): \(error)")
        }
        FileHandle.standardError.write("done \(id)\n".data(using: .utf8)!)
    }
    writeFile(outTsv, tsv)
    writeFile(outJson, jsonString([
        "status": "unverified_hypotheses_not_benchmark",
        "model": "nemotron-3.5-asr-streaming-multilingual-0.6b",
        "model_dir": modelDir,
        "language": languageCode,
        "vocab_terms": vocabTerms.count,
        "load_s": loadSeconds,
        "audio_s": totalAudio,
        "inference_s": totalInference,
        "rtf": totalAudio > 0 ? totalInference / totalAudio : 0,
        "segments": timings,
    ]))
}

func runStreamingNemotron(_ args: Args) async {
    guard let modelDir = args.value("model-dir"), let listPath = args.value("files"),
          let outJson = args.value("out-json") else {
        fail("нужны --model-dir, --files, --out-json")
    }
    let files = readList(listPath)
    let languageCode = args.value("language") ?? "ru-RU"
    let realtime = args.flag("realtime")
    let stepMs = Int(args.value("step-ms") ?? "20") ?? 20
    let manager = StreamingNemotronMultilingualAsrManager()

    final class Collector: @unchecked Sendable {
        var partials: [(at: Double, text: String)] = []
        var startedAt = 0.0
    }
    let collector = Collector()

    let loadStart = timestamp()
    do {
        try await manager.loadModels(from: URL(fileURLWithPath: modelDir, isDirectory: true))
    } catch {
        fail("не загрузились модели Nemotron из \(modelDir): \(error)")
    }
    let loadSeconds = timestamp() - loadStart

    await manager.setPartialCallback { text in
        collector.partials.append((timestamp() - collector.startedAt, text))
    }

    var reports: [[String: Any]] = []
    for file in files {
        let id = file.deletingPathExtension().lastPathComponent
        do {
            try await manager.reset()
            await manager.setLanguage(languageCode)
            collector.partials = []
            let samples = try loadPcm16k(file)
            let duration = Double(samples.count) / 16_000.0
            let step = max(1, 16_000 * stepMs / 1000)
            let started = timestamp()
            collector.startedAt = started
            var offset = 0
            while offset < samples.count {
                let end = min(offset + step, samples.count)
                _ = try await manager.process(samples: Array(samples[offset..<end]))
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
            let finished = try await manager.finishWithTokenTimings()
            let finalText = finished.text
            let finishSeconds = timestamp() - finishStart
            let timings = finished.timings.map { ["text": $0.token, "start": Double($0.startTime), "end": Double($0.endTime)] }
            let wall = timestamp() - started
            let partials = collector.partials.map { ["at_s": $0.at, "text": $0.text] }
            reports.append([
                "segment_id": id,
                "audio_s": duration,
                "wall_s": wall,
                "rtf": duration > 0 ? wall / duration : 0,
                "finish_s": finishSeconds,
                "final": finalText.replacingOccurrences(of: "\t", with: " "),
                "partials": partials,
                "partial_count": partials.count,
                "token_timings": timings,
            ])
        } catch {
            fail("ошибка на \(id): \(error)")
        }
        FileHandle.standardError.write("done \(id)\n".data(using: .utf8)!)
    }
    writeFile(outJson, jsonString([
        "status": "streaming_measurement_not_benchmark",
        "model": "nemotron-3.5-asr-streaming-multilingual-0.6b",
        "model_dir": modelDir,
        "language": languageCode,
        "realtime": realtime,
        "step_ms": stepMs,
        "load_s": loadSeconds,
        "segments": reports,
    ]))
}
