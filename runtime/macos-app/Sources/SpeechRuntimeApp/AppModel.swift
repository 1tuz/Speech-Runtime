#if os(macOS)
import AppKit
import Combine
import Foundation
import ProcessMetrics
import RuntimeUIModel
import ServiceManagement

@MainActor
final class AppModel: ObservableObject {
    enum Phase: String { case off, downloading, preparing, starting, idle, ready, listening, processing, stopping, error }
    @Published private(set) var phase: Phase = .off
    @Published private(set) var running = false
    @Published private(set) var busy = false
    @Published private(set) var snapshot: RuntimeSnapshot?
    @Published private(set) var failure: String?
    @Published private(set) var downloadProgress: Double?
    @Published private(set) var cpuPercent: Double?
    @Published private(set) var rssBytes: UInt64?
    @Published private(set) var loginEnabled = false
    @Published private(set) var loginNeedsApproval = false
    @Published var settingsVisible = false
    @Published var idleMinutes: Int = UserDefaults.standard.object(forKey: "idleMinutes") as? Int ?? 15

    let api = RuntimeAPI()
    private let agent = AgentManager()
    private let models = ModelStore()
    private var operation: Task<Void, Never>?
    private var monitor: Task<Void, Never>?
    private var panelVisible = false
    private var rates: [Int32: CPURate] = [:]
    private var desiredEnabled: Bool {
        get { UserDefaults.standard.bool(forKey: "serviceEnabled") }
        set { UserDefaults.standard.set(newValue, forKey: "serviceEnabled") }
    }
    var sessions: Int { snapshot?.streams ?? 0 }
    var queue: Int? { snapshot?.selectedModel?.interactive }
    var neuralBytes: UInt64? { snapshot?.worker?.memory?.neural }
    var statusText: String { L("state." + phase.rawValue) }
    var statusSymbol: String {
        if phase == .error { return "exclamationmark.waveform" }
        if !running { return "waveform.slash" }
        return phase == .processing ? "waveform.circle.fill" : "waveform"
    }

    func launch() {
        refreshLogin()
        beginMonitoring()
        Task {
            await refresh()
            if desiredEnabled && !running { enable() }
        }
    }
    func visibilityChanged(_ visible: Bool) {
        panelVisible = visible
        rates.removeAll()
        cpuPercent = nil
        beginMonitoring()
    }
    private func beginMonitoring() {
        monitor?.cancel()
        monitor = Task { [weak self] in
            while !Task.isCancelled {
                guard let self else { return }
                if !self.busy { await self.refresh() }
                let nanos: UInt64 = self.panelVisible ? 2_000_000_000 : 10_000_000_000
                do { try await Task.sleep(nanoseconds: nanos) } catch { return }
            }
        }
    }
    func refresh() async {
        guard !busy else { return }
        refreshLogin()
        guard let pid = await agent.processID() else {
            running = false; snapshot = nil; cpuPercent = nil; rssBytes = nil; rates.removeAll()
            if phase != .error { phase = .off }
            return
        }
        do {
            let status = try await api.health()
            guard !busy else { return }
            guard status.memory?.pid == pid else { throw AppFailure(L("error.port")) }
            snapshot = status; running = true
            if !status.hasWorker { phase = .error; failure = L("error.worker"); return }
            failure = nil
            phase = status.processing ? .processing : (sessions > 0 ? .listening : (status.modelLoaded ? .ready : .idle))
            if panelVisible { updateMetrics(status) }
        } catch {
            running = true; phase = .error; failure = error.localizedDescription
        }
    }
    private func updateMetrics(_ status: RuntimeSnapshot) {
        let pids = Set([ProcessInfo.processInfo.processIdentifier, status.memory?.pid, status.worker?.pid].compactMap { $0 })
        var sumCPU = 0.0, sumRSS: UInt64 = 0
        var completeCPU = true, completeMemory = true
        let now = ProcessInfo.processInfo.systemUptime
        for pid in pids {
            let sample = sr_sample_process(pid)
            guard sample.available else { completeCPU = false; completeMemory = false; continue }
            sumRSS += sample.resident_bytes
            var rate = rates[pid] ?? CPURate()
            if let value = rate.sample(identity: "\(pid)/\(sample.started)", time: now, totalNanos: sample.cpu_ns) {
                sumCPU += value
            } else { completeCPU = false }
            rates[pid] = rate
        }
        rates = rates.filter { pids.contains($0.key) }
        cpuPercent = completeCPU ? sumCPU : nil
        rssBytes = completeMemory ? sumRSS : nil
    }

    func togglePower() {
        if busy { return }
        if running {
            if sessions > 0 || snapshot?.processing == true {
                let alert = NSAlert()
                alert.messageText = L("stop.title")
                alert.informativeText = String(format: L("stop.body"), sessions)
                alert.addButton(withTitle: L("power.off")); alert.addButton(withTitle: L("cancel"))
                guard alert.runModal() == .alertFirstButtonReturn else { return }
            }
            disable()
        } else { enable() }
    }
    func enable() {
        guard !busy else { return }
        busy = true; failure = nil; desiredEnabled = true
        operation = Task {
            do {
                // Refuse to adopt or kill a developer's manually started server on the same port.
                if let existing = try? await api.health() {
                    guard let ownedPID = await agent.processID(), existing.memory?.pid == ownedPID else {
                        throw AppFailure(L("error.port"))
                    }
                }
                phase = .downloading; downloadProgress = nil
                try await models.ensureInstalled { [weak self] progress in
                    Task { @MainActor in self?.downloadProgress = progress }
                }
                try Task.checkCancellation()
                phase = .preparing
                try await models.prepare()
                try AppPaths.writeConfiguration(idleSeconds: idleMinutes * 60)
                phase = .starting
                try await agent.start(idleSeconds: idleMinutes * 60)
                // Child readiness is distinct from launchctl bootstrap success.
                var ready = false
                for _ in 0..<40 {
                    try Task.checkCancellation()
                    if let pid = await agent.processID(), let status = try? await api.health(), status.memory?.pid == pid, status.hasWorker {
                        ready = true; break
                    }
                    try await Task.sleep(nanoseconds: 250_000_000)
                }
                guard ready else { throw AppFailure(L("error.ready")) }
                try await api.load()
                busy = false; operation = nil
                await refresh()
            } catch {
                desiredEnabled = false
                busy = false; operation = nil
                if Task.isCancelled || error is CancellationError { phase = .off }
                else { phase = .error; failure = error.localizedDescription }
                running = await agent.processID() != nil
            }
        }
    }
    func cancelSetup() {
        operation?.cancel()
        desiredEnabled = false
        // The cancellation handler owns and terminates only the preparation helper.
    }
    func disable() {
        guard !busy else { return }
        busy = true; phase = .stopping; desiredEnabled = false
        operation = Task {
            do {
                let owned = [snapshot?.memory?.pid, snapshot?.worker?.pid].compactMap { $0 }.map {
                    (pid: $0, start: sr_sample_process($0).started)
                }
                try await agent.stop()
                var allExited = owned.isEmpty
                for _ in 0..<50 {
                    allExited = owned.allSatisfy {
                        let sample = sr_sample_process($0.pid)
                        return !sample.available || sample.started != $0.start
                    }
                    if allExited { break }
                    try await Task.sleep(nanoseconds: 100_000_000)
                }
                guard allExited else { throw AppFailure(L("error.stop")) }
                snapshot = nil; running = false; phase = .off
                cpuPercent = nil; rssBytes = nil; failure = nil; rates.removeAll()
            } catch { phase = .error; failure = error.localizedDescription }
            busy = false; operation = nil
        }
    }
    func setLogin(_ value: Bool) {
        do {
            if value { try SMAppService.mainApp.register() }
            else { try SMAppService.mainApp.unregister() }
            refreshLogin()
        } catch { failure = error.localizedDescription; refreshLogin() }
    }
    func refreshLogin() {
        loginEnabled = SMAppService.mainApp.status == .enabled
        loginNeedsApproval = SMAppService.mainApp.status == .requiresApproval
    }
    func openLoginSettings() { SMAppService.openSystemSettingsLoginItems() }
    func saveIdlePreference() {
        idleMinutes = [5, 15, 30, 60].contains(idleMinutes) ? idleMinutes : 15
        UserDefaults.standard.set(idleMinutes, forKey: "idleMinutes")
        // Applies at next service start; no silent interruption of current sessions.
    }
    func showLog() {
        if FileManager.default.fileExists(atPath: AppPaths.log.path) { NSWorkspace.shared.open(AppPaths.log) }
        else { NSWorkspace.shared.open(AppPaths.support) }
    }
    func revealFiles() { NSWorkspace.shared.open(AppPaths.support) }
    func quitUI() {
        if busy { cancelSetup() }
        monitor?.cancel()
        NSApp.terminate(nil)
    }
}
#endif
