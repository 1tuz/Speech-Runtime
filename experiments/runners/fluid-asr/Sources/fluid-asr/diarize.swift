import FluidAudio
import Foundation

// Диаризация целых встреч Veche-bench и запись RTTM.
//   fluid-asr diarize --files meetings.txt --out-dir DIR
//
// Используется offline-пайплайн DiarizerManager (VBx): сегментация, эмбеддинги,
// кластеризация по всему файлу. Перекрытия он не разрешает — это ограничение
// метода, а не настройка.

func runDiarize(_ args: Args) async {
    guard let listPath = args.value("files"), let outDir = args.value("out-dir") else {
        fail("нужны --files и --out-dir")
    }
    let files = readList(listPath)
    try? FileManager.default.createDirectory(atPath: outDir, withIntermediateDirectories: true)

    let loadStart = timestamp()
    let models: DiarizerModels
    do {
        models = try await DiarizerModels.downloadIfNeeded()
    } catch {
        fail("не загрузились модели диаризации: \(error)")
    }
    let loadSeconds = timestamp() - loadStart
    let diarizer = DiarizerManager()
    diarizer.initialize(models: models)

    var summary: [[String: Any]] = []
    for file in files {
        let id = file.deletingPathExtension().lastPathComponent
        let samples: [Float]
        do {
            samples = try loadPcm16k(file)
        } catch {
            fail("не читается \(id): \(error)")
        }
        let audioSeconds = Double(samples.count) / 16_000.0
        let start = timestamp()
        do {
            let result = try diarizer.performCompleteDiarization(samples, sampleRate: 16_000)
            let seconds = timestamp() - start
            var lines: [String] = []
            for segment in result.segments.sorted(by: { $0.startTimeSeconds < $1.startTimeSeconds }) {
                let begin = Double(segment.startTimeSeconds)
                let length = Double(segment.endTimeSeconds) - begin
                lines.append(String(
                    format: "SPEAKER %@ 1 %.3f %.3f <NA> <NA> %@ <NA> <NA>",
                    id, begin, length, segment.speakerId))
            }
            writeFile("\(outDir)/\(id).rttm", lines.joined(separator: "\n") + "\n")
            let speakers = Set(result.segments.map(\.speakerId)).count
            summary.append([
                "recording_id": id,
                "audio_s": audioSeconds,
                "inference_s": seconds,
                "rtf": audioSeconds > 0 ? seconds / audioSeconds : 0,
                "segments": result.segments.count,
                "speakers": speakers,
            ])
        } catch {
            fail("ошибка диаризации \(id): \(error)")
        }
        FileHandle.standardError.write("done \(id)\n".data(using: .utf8)!)
    }
    writeFile("\(outDir)/diarize-run.json", jsonString([
        "status": "diarization_measurement_not_benchmark",
        "engine": "FluidAudio DiarizerManager (VBx offline)",
        "load_s": loadSeconds,
        "meetings": summary,
    ]))
}
