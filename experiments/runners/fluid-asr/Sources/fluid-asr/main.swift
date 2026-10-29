import AVFoundation
import FluidAudio
import Foundation

// Команды:
//   fluid-asr asr --model v3|ultra --files list.txt --out-tsv hyp.tsv --out-json run.json
//   fluid-asr nemotron --model-dir DIR --files list.txt --out-tsv hyp.tsv --out-json run.json [--vocab words.txt]
//   fluid-asr stream-nemotron --model-dir DIR --files list.txt --out-json stream.json [--realtime]
//
// asr и nemotron читают список путей (по одному в строке) и пишут TSV `<id>\t<text>`.
// stream-nemotron подаёт тот же файл порциями и по умолчанию гонит их без задержки,
// с --realtime — по часам. Частичные результаты пишутся в JSON вместе с таймингами.

struct Args {
    var values: [String: String] = [:]
    var flags: Set<String> = []

    init(_ raw: [String]) {
        var index = 0
        while index < raw.count {
            let item = raw[index]
            if item.hasPrefix("--") {
                let name = String(item.dropFirst(2))
                if index + 1 < raw.count && !raw[index + 1].hasPrefix("--") {
                    values[name] = raw[index + 1]
                    index += 2
                } else {
                    flags.insert(name)
                    index += 1
                }
            } else {
                index += 1
            }
        }
    }

    func value(_ name: String) -> String? { values[name] }
    func flag(_ name: String) -> Bool { flags.contains(name) }
}

func fail(_ message: String) -> Never {
    FileHandle.standardError.write((message + "\n").data(using: .utf8)!)
    exit(1)
}

func readList(_ path: String) -> [URL] {
    guard let text = try? String(contentsOfFile: path, encoding: .utf8) else {
        fail("не читается список файлов: \(path)")
    }
    return text.split(whereSeparator: \.isNewline)
        .map { $0.trimmingCharacters(in: .whitespaces) }
        .filter { !$0.isEmpty && !$0.hasPrefix("#") }
        .map { URL(fileURLWithPath: $0) }
}

func writeFile(_ path: String, _ text: String) {
    do {
        try text.write(toFile: path, atomically: true, encoding: .utf8)
    } catch {
        fail("не записывается \(path): \(error)")
    }
}

func jsonString(_ value: Any) -> String {
    let data = try! JSONSerialization.data(withJSONObject: value, options: [.prettyPrinted, .sortedKeys])
    return String(data: data, encoding: .utf8)!
}

func modelVersion(_ raw: String?) -> AsrModelVersion {
    switch raw ?? "v3" {
    case "v3": return .v3
    case "ultra": return .ultra
    case "redux": return .redux
    default: fail("неизвестная модель: \(raw ?? "")")
    }
}

func language(_ raw: String?) -> Language? {
    guard let raw else { return nil }
    switch raw {
    case "ru": return .russian
    case "en": return .english
    default: fail("неизвестный язык: \(raw)")
    }
}

func timestamp() -> Double { Date().timeIntervalSince1970 }

@main
struct Runner {
    static func main() async {
        var raw = Array(CommandLine.arguments.dropFirst())
        guard let command = raw.first else {
            fail("нужна команда: asr | nemotron | stream-nemotron | diarize")
        }
        raw.removeFirst()
        let args = Args(raw)
        switch command {
        case "asr": await runBatchAsr(args)
        case "nemotron": await runNemotronBatch(args)
        case "stream-nemotron": await runStreamingNemotron(args)
        case "diarize": await runDiarize(args)
        default: fail("неизвестная команда: \(command)")
        }
    }
}
