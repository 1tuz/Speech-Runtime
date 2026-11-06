#if os(macOS)
import AppKit
import Darwin
import Foundation
import RuntimeUIModel

private enum Localization {
    static let bundle: Bundle = {
        // SwiftPM's generated accessor uses the build path as a fallback. Never depend
        // on that absolute path in an installed app.
        if Bundle.main.bundleURL.pathExtension == "app",
           let resource = Bundle.main.resourceURL?.appendingPathComponent("SpeechRuntimeMenu_SpeechRuntimeApp.bundle"),
           let bundle = Bundle(url: resource) { return bundle }
        return Bundle.module
    }()
}
func L(_ key: String) -> String { NSLocalizedString(key, bundle: Localization.bundle, comment: "") }

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
