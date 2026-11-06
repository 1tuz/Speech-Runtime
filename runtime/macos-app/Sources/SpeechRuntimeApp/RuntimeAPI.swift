#if os(macOS)
import Foundation
import Darwin
import ProcessMetrics
import RuntimeUIModel

actor RuntimeAPI {
    static let endpoint = URL(string: "http://127.0.0.1:8477")!
    private let session: URLSession
    init() {
        let config = URLSessionConfiguration.ephemeral
        config.timeoutIntervalForRequest = 8
        config.timeoutIntervalForResource = 180
        config.requestCachePolicy = .reloadIgnoringLocalCacheData
        config.urlCache = nil
        session = URLSession(configuration: config)
    }
    func health() async throws -> RuntimeSnapshot {
        try JSONDecoder().decode(RuntimeSnapshot.self, from: await send("/health", authenticated: false))
    }
    func load() async throws {
        do {
            _ = try await send("/v1/models/load", body: ["model": ModelSpec.id, "backend": "ane"], timeout: 180)
        } catch let error as URLError where [.timedOut, .networkConnectionLost].contains(error.code) {
            // The existing server has a connection deadline. A cold ANE compile can
            // outlive it even though the worker still finishes loading. Observe
            // readiness; do not enqueue duplicate loads or silently switch to GPU.
            let deadline = ProcessInfo.processInfo.systemUptime + 150
            while ProcessInfo.processInfo.systemUptime < deadline {
                try Task.checkCancellation()
                if let status = try? await health(), status.modelLoaded { return }
                try await Task.sleep(nanoseconds: 1_000_000_000)
            }
            throw error
        }
    }
    private func send(_ path: String, body: [String: String]? = nil,
                      authenticated: Bool = true, timeout: TimeInterval = 8) async throws -> Data {
        var request = URLRequest(url: Self.endpoint.appendingPathComponent(String(path.dropFirst())))
        request.timeoutInterval = timeout
        if authenticated {
            let token = try String(contentsOf: AppPaths.token, encoding: .utf8).trimmingCharacters(in: .whitespacesAndNewlines)
            guard !token.isEmpty else { throw AppFailure(L("error.token")) }
            request.setValue("Bearer " + token, forHTTPHeaderField: "Authorization")
        }
        if let body {
            request.httpMethod = "POST"
            request.setValue("application/json", forHTTPHeaderField: "Content-Type")
            request.httpBody = try JSONSerialization.data(withJSONObject: body)
        }
        let (data, response) = try await session.data(for: request)
        guard let http = response as? HTTPURLResponse else { throw AppFailure(L("error.response")) }
        guard (200..<300).contains(http.statusCode) else {
            let object = (try? JSONSerialization.jsonObject(with: data)) as? [String: Any]
            throw AppFailure(object?["error"] as? String ?? "HTTP \(http.statusCode)")
        }
        return data
    }
}

/// Current-login LaunchAgent; login persistence belongs to SMAppService.mainApp.
/// Keeping the plist out of ~/Library/LaunchAgents makes the two toggles independent.
@MainActor
final class AgentManager {
    private let launchctl = URL(fileURLWithPath: "/bin/launchctl")
    private var domain: String { "gui/\(getuid())" }
    private var target: String { "\(domain)/\(AppPaths.label)" }
    private var knownProcess: (pid: Int32, started: UInt64)?

    func processID() async -> Int32? {
        if let known = knownProcess {
            let sample = sr_sample_process(known.pid)
            if sample.available && sample.started == known.started { return known.pid }
            knownProcess = nil
        }
        guard let result = try? await Command.run(launchctl, ["print", target], timeout: 8), result.status == 0 else { return nil }
        for line in result.output.split(separator: "\n") {
            let parts = line.trimmingCharacters(in: .whitespaces).components(separatedBy: " = ")
            if parts.count == 2, parts[0] == "pid", let pid = Int32(parts[1]) {
                let sample = sr_sample_process(pid)
                if sample.available { knownProcess = (pid, sample.started) }
                return pid
            }
        }
        return nil
    }
    func start(idleSeconds: Int) async throws {
        for name in ["speech-runtime", "sr-worker"] {
            guard FileManager.default.isExecutableFile(atPath: AppPaths.executable(name).path) else {
                throw AppFailure(L("error.bundle") + " " + name)
            }
        }
        let result = try await Command.run(launchctl, ["print", target], timeout: 8)
        if result.status == 0 {
            let start = try await Command.run(launchctl, ["kickstart", target], timeout: 10)
            guard start.status == 0 else { throw AppFailure(start.output) }
            return
        }
        let plist = RuntimeFiles.agentPlist(binary: AppPaths.executable("speech-runtime").path,
            worker: AppPaths.executable("sr-worker").path, config: AppPaths.config.path,
            token: AppPaths.token.path, label: AppPaths.label, idleSeconds: idleSeconds)
        try AppPaths.writePrivate(PropertyListSerialization.data(fromPropertyList: plist, format: .xml, options: 0), to: AppPaths.agent)
        let start = try await Command.run(launchctl, ["bootstrap", domain, AppPaths.agent.path], timeout: 15)
        guard start.status == 0 else { throw AppFailure(L("error.launch") + "\n" + start.output) }
    }
    func stop() async throws {
        let present = try await Command.run(launchctl, ["print", target], timeout: 8)
        guard present.status == 0 else { return }
        let stop = try await Command.run(launchctl, ["bootout", target], timeout: 15)
        guard stop.status == 0 else { throw AppFailure(L("error.stop") + "\n" + stop.output) }
        knownProcess = nil
        try? FileManager.default.removeItem(at: AppPaths.agent)
    }
}
#endif
