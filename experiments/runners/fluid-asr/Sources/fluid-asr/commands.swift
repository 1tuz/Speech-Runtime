import AVFoundation
import FluidAudio
import Foundation

/// Читает WAV как 16 кГц mono PCM. Формат проверяется, а не пересчитывается:
/// все входы эксперимента подготовлены ffmpeg в этом формате, и тихое
/// ресемплирование скрыло бы расхождение.
func loadPcm16k(_ url: URL) throws -> [Float] {
    let data = try Data(contentsOf: url)
    guard data.count > 44, data[0..<4].elementsEqual("RIFF".utf8), data[8..<12].elementsEqual("WAVE".utf8) else {
        fail("не WAV: \(url.lastPathComponent)")
    }
    var offset = 12
    var format: (channels: Int, rate: Int, bits: Int, tag: Int)? = nil
    var payload: Range<Int>? = nil
    while offset + 8 <= data.count {
        let id = String(decoding: data[offset..<offset + 4], as: UTF8.self)
        let size = Int(data[offset + 4..<offset + 8].withUnsafeBytes { $0.loadUnaligned(as: UInt32.self) })
        let body = offset + 8
        if id == "fmt " {
            let channels = Int(data[body + 2..<body + 4].withUnsafeBytes { $0.loadUnaligned(as: UInt16.self) })
            let rate = Int(data[body + 4..<body + 8].withUnsafeBytes { $0.loadUnaligned(as: UInt32.self) })
            let bits = Int(data[body + 14..<body + 16].withUnsafeBytes { $0.loadUnaligned(as: UInt16.self) })
            let tag = Int(data[body..<body + 2].withUnsafeBytes { $0.loadUnaligned(as: UInt16.self) })
            format = (channels, rate, bits, tag)
        } else if id == "data" {
            payload = body..<min(body + size, data.count)
        }
        offset = body + size + (size % 2)
    }
    guard let format, let payload else { fail("нет fmt/data в \(url.lastPathComponent)") }
    guard format.channels == 1, format.rate == 16_000 else {
        fail("ожидался 16 кГц mono, получено \(format) в \(url.lastPathComponent)")
    }
    let raw = data[payload]
    if format.bits == 32 && format.tag == 3 {
        // FLEURS публикует float32; читаем без конвертации, чтобы не менять звук.
        var samples = [Float](repeating: 0, count: raw.count / 4)
        raw.withUnsafeBytes { buffer in
            for index in 0..<samples.count {
                samples[index] = buffer.loadUnaligned(fromByteOffset: index * 4, as: Float.self)
            }
        }
        return samples
    }
    guard format.bits == 16, format.tag == 1 else {
        fail("ожидался PCM s16 или float32, получено \(format) в \(url.lastPathComponent)")
    }
    var samples = [Float](repeating: 0, count: raw.count / 2)
    raw.withUnsafeBytes { buffer in
        for index in 0..<samples.count {
            samples[index] = Float(buffer.loadUnaligned(fromByteOffset: index * 2, as: Int16.self)) / 32_768.0
        }
    }
    return samples
}

func runBatchAsr(_ args: Args) async {
    guard let listPath = args.value("files"), let outTsv = args.value("out-tsv"), let outJson = args.value("out-json") else {
        fail("нужны --files, --out-tsv, --out-json")
    }
    let files = readList(listPath)
    let version = modelVersion(args.value("model"))
    let lang = language(args.value("language"))

    let loadStart = timestamp()
    let models: AsrModels
    do {
        models = try await AsrModels.downloadAndLoad(version: version)
    } catch {
        fail("не загрузились модели \(version): \(error)")
    }
    let manager = AsrManager(config: .default)
    do {
        try await manager.loadModels(models)
    } catch {
        fail("не инициализировался менеджер: \(error)")
    }
    let loadSeconds = timestamp() - loadStart

    var tsv = ""
    var timings: [[String: Any]] = []
    var totalAudio = 0.0
    var totalInference = 0.0
    for file in files {
        let id = file.deletingPathExtension().lastPathComponent
        var state = await TdtDecoderState.make(decoderLayers: manager.decoderLayerCount)
        let start = timestamp()
        do {
            let result = try await manager.transcribe(file, decoderState: &state, language: lang)
            let seconds = timestamp() - start
            let text = result.text.replacingOccurrences(of: "\n", with: " ").replacingOccurrences(of: "\t", with: " ")
            tsv += "\(id)\t\(text)\n"
            let duration = Double(try loadPcm16k(file).count) / 16_000.0
            totalAudio += duration
            totalInference += seconds
            timings.append(["segment_id": id, "inference_s": seconds, "audio_s": duration])
        } catch {
            fail("ошибка на \(id): \(error)")
        }
        FileHandle.standardError.write("done \(id)\n".data(using: .utf8)!)
    }
    writeFile(outTsv, tsv)
    writeFile(outJson, jsonString([
        "status": "unverified_hypotheses_not_benchmark",
        "model_version": args.value("model") ?? "v3",
        "language": args.value("language") ?? "auto",
        "load_s": loadSeconds,
        "audio_s": totalAudio,
        "inference_s": totalInference,
        "rtf": totalAudio > 0 ? totalInference / totalAudio : 0,
        "segments": timings,
    ]))
}
