import Foundation

/// Optional model *assets*. A downloaded weight file is not an implemented inference feature.
/// Keep this manifest independent of SwiftUI so it can be validated on Linux and in tests.
public enum OptionalComponent: String, CaseIterable, Hashable, Identifiable, Sendable {
    case smartTurn = "smart-turn"
    case weSpeaker = "we-speaker"
    case localVQE = "local-vqe"
    case lsEEND = "ls-eend"
    case forcedAlignment = "forced-alignment"

    public var id: String { rawValue }
    public var titleKey: String { "optional.\(rawValue).title" }
    public var detailKey: String { "optional.\(rawValue).detail" }
    public var sizeKey: String { "optional.\(rawValue).size" }
    public var icon: String {
        switch self {
        case .smartTurn: "brain.head.profile"
        case .weSpeaker: "person.2.wave.2"
        case .localVQE: "waveform.path"
        case .lsEEND: "person.3.sequence"
        case .forcedAlignment: "text.word.spacing"
        }
    }

    public var asset: OptionalAssetSpec? {
        switch self {
        case .smartTurn:
            return OptionalAssetSpec(
                fileName: "smart-turn-v3.2-cpu.onnx", bytes: 8_679_182,
                sha256: "2bb026316b14a660486a75b1733cd3fbab8c2fd0314dc9af7be49f8cca967e4f",
                urlString: "https://huggingface.co/pipecat-ai/smart-turn-v3/resolve/main/smart-turn-v3.2-cpu.onnx")
        case .weSpeaker:
            return OptionalAssetSpec(
                fileName: "voxceleb_resnet34_LM.onnx", bytes: 26_530_309,
                sha256: "7bb2f06e9df17cdf1ef14ee8a15ab08ed28e8d0ef5054ee135741560df2ec068",
                urlString: "https://huggingface.co/Wespeaker/wespeaker-voxceleb-resnet34-LM/resolve/main/voxceleb_resnet34_LM.onnx")
        case .localVQE, .lsEEND, .forcedAlignment:
            // Multi-asset CoreML packages and aligner need a validated, versioned driver.
            // Do NOT expose an install button until inference + lifecycle support exists.
            return nil
        }
    }
}

public struct OptionalAssetSpec: Sendable {
    public let fileName: String
    public let bytes: Int64
    public let sha256: String
    public let urlString: String

    public var url: URL { URL(string: urlString)! }
    public var isValid: Bool {
        bytes > 0 && sha256.count == 64 && sha256.utf8.allSatisfy {
            ($0 >= 48 && $0 <= 57) || ($0 >= 97 && $0 <= 102)
        } && url.scheme == "https" && url.host == "huggingface.co" && !fileName.contains("/")
    }
}
