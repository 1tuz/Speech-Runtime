import AVFoundation
import FluidAudio
import Foundation

// A/B для vocabulary boosting на Parakeet TDT v3 (подход 2: отдельный CTC-энкодер).
//
//   fluid-asr parakeet-boost --files list.txt --out-dir DIR [--vocab words.txt]
//
// Прогон идёт через SlidingWindowAsrManager: boosting работает только там, где
// окно рескорится CTC-свидетельствами. Окно по умолчанию 11+2+2 с — это не
// настройка под корпус, а штатная раскладка движка.

func runParakeetBoost(_ args: Args) async {
    guard let listPath = args.value("files"), let outDir = args.value("out-dir") else {
        fail("нужны --files и --out-dir")
    }
    let files = readList(listPath)
    let languageCode = args.value("language")
    let useVocabulary = args.value("vocab")
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

    let config = SlidingWindowAsrConfig.streaming.applying(language: languageCode.map { $0 == "ru" ? .russian : .english })
    let loadSeconds = timestamp() - loadStart

    var rows: [String] = []
    var timings: [[String: Any]] = []
    var audioTotal = 0.0
    var inferenceTotal = 0.0
    for file in files {
        let id = file.deletingPathExtension().lastPathComponent
        let samples = try! loadPcm16k(file)
        let duration = Double(samples.count) / 16_000.0
        let start = timestamp()
        do {
            // Новый экземпляр на файл: finish() закрывает входной AsyncStream,
            // повторный startStreaming на том же объекте молча даёт пустой текст.
            let manager = SlidingWindowAsrManager(config: config)
            try await manager.loadModels(models)
            if let ctcModels, !terms.isEmpty {
                try await manager.configureVocabularyBoosting(
                    vocabulary: CustomVocabularyContext(terms: terms), ctcModels: ctcModels)
            }
            try await manager.startStreaming(source: .microphone)
            // Порции по 100 мс: мелкие окна дают движку столько же поводов
            // подтвердить текст, сколько дал бы микрофон.
            let step = 1_600
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
            }
            let text = try await manager.finish()
            let seconds = timestamp() - start
            rows.append("\(id)\t\(text.replacingOccurrences(of: "\t", with: " "))")
            audioTotal += duration
            inferenceTotal += seconds
            timings.append(["segment_id": id, "audio_s": duration, "inference_s": seconds])
        } catch {
            fail("ошибка на \(id): \(error)")
        }
        FileHandle.standardError.write("done \(id)\n".data(using: .utf8)!)
    }

    let suffix = terms.isEmpty ? "plain" : "vocab"
    writeFile("\(outDir)/parakeet-\(suffix).tsv", rows.joined(separator: "\n") + "\n")
    writeFile("\(outDir)/parakeet-\(suffix).json", jsonString([
        "status": "unverified_hypotheses_not_benchmark",
        "model": "parakeet-tdt-0.6b-v3",
        "engine": "SlidingWindowAsrManager, 11+2+2 s window, CTC-110m rescorer",
        "language": languageCode ?? "auto",
        "vocab_terms": terms.count,
        "load_s": loadSeconds,
        "audio_s": audioTotal,
        "inference_s": inferenceTotal,
        "rtf": audioTotal > 0 ? inferenceTotal / audioTotal : 0,
        "segments": timings,
    ]))
}
