import AVFoundation
import CoreML
import FluidAudio
import Foundation

// Unified FastConformer-RNNT (nemotron-labs-unified-asr-0.6b, 25 языков, prompt ru=11):
// офлайн-батч и стриминг в одном чекпойнте.
//
// fluid-asr unified --files list.txt --out-dir DIR [--int8] [--streaming]
//     [--left 70 --chunk 13 --right 13]
//
// Батч: UnifiedAsrManager, окно 15 с. Стриминг: StreamingUnifiedAsrManager,
// окно [left | chunk | right] в 80-мс фреймах, задержка = chunk + right.
// Язык зашит в family (ru); файлы подаются без задержки — это гипотезы, не
// замер задержки; RTF считается по чистому времени инференса.

/// 16 кГц mono Float32 из любого входного аудио.
func readMono16kSamples(_ url: URL) throws -> (samples: [Float], seconds: Double) {
    let file = try AVAudioFile(forReading: url)
    guard let format = AVAudioFormat(commonFormat: .pcmFormatFloat32, sampleRate: 16000, channels: 1, interleaved: false) else {
        throw NSError(domain: "unified", code: 1)
    }
    let converter = AVAudioConverter(from: file.processingFormat, to: format)!
    let ratio = 16000.0 / file.fileFormat.sampleRate
    let outCapacity = AVAudioFrameCount((Double(file.length) * ratio).rounded(.up)) + 4096
    guard let out = AVAudioPCMBuffer(pcmFormat: format, frameCapacity: outCapacity) else {
        throw NSError(domain: "unified", code: 2)
    }
    var consumed = false
    // AVAudioFile без буфера: читаем по одному буферу исходного формата.
    guard let srcFormat = AVAudioFormat(commonFormat: .pcmFormatFloat32, sampleRate: file.fileFormat.sampleRate, channels: file.fileFormat.channelCount, interleaved: false),
          let src = AVAudioPCMBuffer(pcmFormat: srcFormat, frameCapacity: 8192) else {
        throw NSError(domain: "unified", code: 3)
    }
    let inputBlock: AVAudioConverterInputBlock = { _, status in
        if consumed { status.pointee = .endOfStream; return nil }
        do { try file.read(into: src) } catch {
            status.pointee = .endOfStream
            return nil
        }
        if src.frameLength == 0 {
            consumed = true
            status.pointee = .endOfStream
            return nil
        }
        status.pointee = .haveData
        return src
    }
    var error: NSError?
    converter.convert(to: out, error: &error, withInputFrom: inputBlock)
    if let error { throw error }
    guard let channel = out.floatChannelData else { throw NSError(domain: "unified", code: 4) }
    let samples = Array(UnsafeBufferPointer(start: channel[0], count: Int(out.frameLength)))
    return (samples, Double(out.frameLength) / 16000.0)
}

func runUnified(_ args: Args) async {
    guard let filesArg = args.value("files"), let outDir = args.value("out-dir") else {
        fail("нужны --files и --out-dir")
    }
    let urls = readList(filesArg)
    let streaming = args.flag("streaming")
    let isInt8 = args.flag("int8")
    let config = UnifiedConfig(
        leftFrames: Int(args.value("left") ?? "70") ?? 70,
        chunkFrames: Int(args.value("chunk") ?? "13") ?? 13,
        rightFrames: Int(args.value("right") ?? "13") ?? 13,
        family: .nemotronMultilingual(.ru))
    let precision: UnifiedEncoderPrecision = isInt8 ? .int8 : .fp16
    let suffix = "\(streaming ? "stream" : "offline")-\(isInt8 ? "int8" : "fp16")"

    try? FileManager.default.createDirectory(atPath: outDir, withIntermediateDirectories: true)
    let loadStart = Date()
    var rows: [String] = []
    var timings: [[String: Any]] = []
    var audioTotal = 0.0
    var inferenceTotal = 0.0
    let ml = MLModelConfiguration()
    ml.computeUnits = .all

    if streaming {
        let manager = StreamingUnifiedAsrManager(configuration: ml, config: config, encoderPrecision: precision)
        do {
            try await manager.loadModels()
        } catch {
            fail("загрузка unified streaming не удалась: \(error)")
        }
        for url in urls {
            guard let audio = try? readMono16kSamples(url) else {
                FileHandle.standardError.write(("skip \(url.lastPathComponent)\n").data(using: .utf8)!)
                continue
            }
            guard let buffer = AVAudioPCMBuffer(pcmFormat: AVAudioFormat(commonFormat: .pcmFormatFloat32, sampleRate: 16000, channels: 1, interleaved: false)!,
                                                frameCapacity: AVAudioFrameCount(audio.samples.count)) else {
                fail("буфер \(url.lastPathComponent)")
            }
            buffer.floatChannelData![0].update(from: audio.samples, count: audio.samples.count)
            buffer.frameLength = AVAudioFrameCount(audio.samples.count)
            let start = Date()
            do {
                try await manager.appendAudio(buffer)
                let text = try await manager.finish()
                let inference = Date().timeIntervalSince(start)
                audioTotal += audio.seconds
                inferenceTotal += inference
                let id = url.deletingPathExtension().lastPathComponent
                rows.append("\(id)\t\(text.replacingOccurrences(of: "\n", with: " "))")
                timings.append(["segment_id": id, "audio_s": audio.seconds, "inference_s": inference])
                FileHandle.standardError.write(("done \(id)\n").data(using: .utf8)!)
            } catch {
                fail("ошибка \(url.lastPathComponent): \(error)")
            }
        }
    } else {
        let manager = UnifiedAsrManager(configuration: ml, config: config, encoderPrecision: precision)
        do {
            try await manager.loadModels()
        } catch {
            fail("загрузка unified offline не удалась: \(error)")
        }
        for url in urls {
            guard let audio = try? readMono16kSamples(url) else {
                FileHandle.standardError.write(("skip \(url.lastPathComponent)\n").data(using: .utf8)!)
                continue
            }
            let start = Date()
            do {
                let text = try await manager.transcribe(audio.samples)
                let inference = Date().timeIntervalSince(start)
                audioTotal += audio.seconds
                inferenceTotal += inference
                let id = url.deletingPathExtension().lastPathComponent
                rows.append("\(id)\t\(text.replacingOccurrences(of: "\n", with: " "))")
                timings.append(["segment_id": id, "audio_s": audio.seconds, "inference_s": inference])
                FileHandle.standardError.write(("done \(id)\n").data(using: .utf8)!)
            } catch {
                fail("ошибка \(url.lastPathComponent): \(error)")
            }
        }
    }

    let loadSeconds = Date().timeIntervalSince(loadStart)
    writeFile("\(outDir)/unified-\(suffix).tsv", rows.joined(separator: "\n") + "\n")
    let meta: [String: Any] = [
        "status": "unverified_hypotheses_not_benchmark",
        "model": "nemotron-labs-unified-asr-0.6b",
        "family": "nemotronMultilingual(ru)",
        "engine": "FluidAudio v0.18.0 CoreML",
        "precision": isInt8 ? "int8" : "fp16",
        "mode": streaming ? "streaming" : "offline-15s",
        "window": "\(config.leftFrames)_\(config.chunkFrames)_\(config.rightFrames)",
        "latency_ms_theoretical": config.latencyMs,
        "load_s": loadSeconds,
        "audio_s": audioTotal,
        "inference_s": inferenceTotal,
        "rtf": audioTotal > 0 ? inferenceTotal / audioTotal : 0,
        "segments": timings,
    ]
    writeFile("\(outDir)/unified-\(suffix).json", jsonString(meta))
    print("rtf \(String(format: "%.3f", audioTotal > 0 ? inferenceTotal / audioTotal : 0))")
}
