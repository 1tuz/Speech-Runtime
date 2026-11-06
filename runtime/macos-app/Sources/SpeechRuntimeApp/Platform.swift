#if os(macOS)
import AppKit
import Darwin
import Foundation
import RuntimeUIModel

private enum Localization {
    // SwiftPM's generated accessor uses the build path as a fallback. Never depend
    // on that absolute path in an installed app.
    static let container: Bundle = {
        if Bundle.main.bundleURL.pathExtension == "app",
           let resource = Bundle.main.resourceURL?.appendingPathComponent("SpeechRuntimeMenu_SpeechRuntimeApp.bundle"),
           let bundle = Bundle(url: resource) { return bundle }
        return Bundle.module
    }()
    /// Cached per-language bundle. Rebuilt when "appLanguage" changes so new L() calls
    /// immediately reflect the user's choice. NotificationCenter drives the invalidation.
    private static let lock = NSLock()
    private static var cachedLanguage: AppLanguage?
    private static var cachedBundle: Bundle?
    static func bundle(for language: AppLanguage) -> Bundle {
        lock.lock(); defer { lock.unlock() }
        let lproj = language.lprojName(preferredLanguages: preferredLanguages)
        // The localization-specific path must point at the .lproj DIRECTORY: a
        // .strings file is not a loadable bundle, Bundle(path:) on it returns nil
        // and the old code silently fell back to the container bundle, which
        // resolves through the process-wide AppleLanguages lookup and ignores
        // the in-app preference. That is why switching language did nothing.
        if let stringsPath = container.path(forResource: "Localizable", ofType: "strings",
                                            inDirectory: nil, forLocalization: lproj) {
            let lprojPath = (stringsPath as NSString).deletingLastPathComponent
            if let bundle = Bundle(path: lprojPath) {
                cachedLanguage = language; cachedBundle = bundle
                return bundle
            }
        }
        cachedLanguage = language; cachedBundle = container
        return container
    }
    private static var preferredLanguages: [String] {
        // "system" means follow the OS, not an AppleLanguages override that a user
        // may have set in `defaults` for other apps; keep the lookup to locale data.
        (Locale.preferredLanguages as [String])
    }
}
func L(_ key: String) -> String {
    NSLocalizedString(key, bundle: Localization.bundle(for: .storedValue()), comment: "")
}

extension Notification.Name {
    /// Posted after "appLanguage" changes so cached localization strings rebuild.
    static let appLanguageChanged = Notification.Name("AppLanguageChanged")
}

struct AppFailure: LocalizedError {
    let message: String
    init(_ message: String) { self.message = message }
    var errorDescription: String? { message }
}

enum AppPaths {
    static let support = FileManager.default.homeDirectoryForCurrentUser
        .appendingPathComponent("Library/Application Support/SpeechRuntime", isDirectory: true)
    static let modelDirectory = support.appendingPathComponent("Models", isDirectory: true)
    static let model = modelDirectory.appendingPathComponent(ModelSpec.filename)
    static let token = support.appendingPathComponent("token")
    // Do not overwrite a developer's existing config.json or models.json.
    static let config = support.appendingPathComponent("app-config.json")
    static let agent = support.appendingPathComponent("com.speechruntime.agent.plist")
    static let log = support.appendingPathComponent("daemon.log")
    static let receipt = support.appendingPathComponent("parakeet-receipt.json")
    static let label = "com.speechruntime.agent"
    static var binaryDirectory: URL { Bundle.main.bundleURL.appendingPathComponent("Contents/MacOS") }
    static func executable(_ name: String) -> URL { binaryDirectory.appendingPathComponent(name) }

    static func createDirectories() throws {
        for url in [support, modelDirectory] {
            try FileManager.default.createDirectory(at: url, withIntermediateDirectories: true,
                attributes: [.posixPermissions: 0o700])
        }
    }
    static func writePrivate(_ data: Data, to url: URL) throws {
        try data.write(to: url, options: .atomic)
        try FileManager.default.setAttributes([.posixPermissions: 0o600], ofItemAtPath: url.path)
    }
    static func writeConfiguration(idleSeconds: Int) throws {
        try createDirectories()
        let data = try JSONSerialization.data(withJSONObject:
            RuntimeFiles.modelConfig(path: model.path, idleSeconds: idleSeconds), options: [.prettyPrinted, .sortedKeys])
        try writePrivate(data, to: config)
    }
}

struct CommandResult: Sendable { let status: Int32; let output: String }

/// Owns only its child process. Cancellation never kills processes by name.
private final class ProcessTicket: @unchecked Sendable {
    private let lock = NSLock()
    private var child: Process?
    private var cancelled = false
    func start(_ process: Process) throws {
        lock.lock(); defer { lock.unlock() }
        guard !cancelled else { throw CancellationError() }
        try process.run()
        child = process
    }
    func clear() { lock.lock(); child = nil; lock.unlock() }
    func cancel() {
        lock.lock(); cancelled = true; let process = child; lock.unlock()
        guard let process, process.isRunning else { return }
        process.terminate()
        DispatchQueue.global().asyncAfter(deadline: .now() + 3) {
            if process.isRunning { Darwin.kill(process.processIdentifier, SIGKILL) }
        }
    }
}

enum Command {
    static func run(_ executable: URL, _ arguments: [String], timeout: TimeInterval = 180) async throws -> CommandResult {
        let ticket = ProcessTicket()
        return try await withTaskCancellationHandler {
            try await withCheckedThrowingContinuation { continuation in
                DispatchQueue.global(qos: .utility).async {
                    let child = Process()
                    let pipe = Pipe()
                    child.executableURL = executable
                    child.arguments = arguments
                    child.standardOutput = pipe
                    child.standardError = pipe
                    child.standardInput = FileHandle.nullDevice
                    let deadline = DispatchWorkItem { ticket.cancel() }
                    do {
                        try ticket.start(child)
                        DispatchQueue.global().asyncAfter(deadline: .now() + timeout, execute: deadline)
                        var tail = Data()
                        while true {
                            let data = autoreleasepool { pipe.fileHandleForReading.availableData }
                            if data.isEmpty { break }
                            tail.append(data)
                            if tail.count > 16_384 { tail.removeFirst(tail.count - 16_384) }
                        }
                        child.waitUntilExit()
                        deadline.cancel()
                        ticket.clear()
                        continuation.resume(returning: CommandResult(status: child.terminationStatus,
                            output: String(decoding: tail, as: UTF8.self)))
                    } catch {
                        deadline.cancel(); ticket.clear()
                        continuation.resume(throwing: error)
                    }
                }
            }
        } onCancel: { ticket.cancel() }
    }
}
#endif
