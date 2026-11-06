import Foundation

/// Pure state machine for the first-run setup wizard. The UI layer applies side
/// effects (login registration, idle preference) when the model transitions.
public struct SetupWizardState: Sendable, Equatable {
    public enum Step: Int, CaseIterable, Sendable, Equatable {
        case welcome = 0
        case login = 1
        case idle = 2
        case capabilities = 3
        case ready = 4
    }

    public private(set) var step: Step = .welcome
    public let language: AppLanguage
    public let idleMinutes: Int

    public init(step: Step = .welcome, language: AppLanguage = .storedValue(), idleMinutes: Int = 15) {
        self.step = step
        self.language = language
        self.idleMinutes = idleMinutes
    }

    public var stepNumber: Int { step.rawValue + 1 }
    public var totalSteps: Int { Step.allCases.count }
    public var isLast: Bool { step == .ready }
    public var isFirst: Bool { step == .welcome }

    public mutating func next() {
        if let index = Step.allCases.firstIndex(of: step), index < Step.allCases.count - 1 {
            step = Step.allCases[index + 1]
        }
    }
    public mutating func back() {
        if let index = Step.allCases.firstIndex(of: step), index > 0 {
            step = Step.allCases[index - 1]
        }
    }
}

/// Fixed capability facts of the current prototype, surfaced so other applications
/// (and agents) can see what the local OpenAI-compatible API actually supports.
/// These are not read from /health: the worker/daemon do not report capabilities yet.
/// Source: the shipped prototype's protocol behavior (README "Other applications").
public enum RuntimeCapabilities: Sendable, Equatable {
    /// Streaming sessions: audio chunks can be sent while the stream is open.
    public static let streaming = true
    /// Only final results are produced; there are no interim/partial hypotheses.
    public static let interim = false
    /// Final transcripts carry token timestamps.
    public static let timestamps = true
    /// No speaker diarization in this prototype.
    public static let diarization = false
}
