#if os(macOS)
import Combine
import CryptoKit
import Foundation
import RuntimeUIModel

/// Download manager for *optional weight files*, not a pretend inference backend.
/// The current daemon does not load these; a future driver must opt in explicitly.
@MainActor
final class OptionalComponentsStore: ObservableObject {
    enum Status: Equatable {
        case checking, absent, downloading(Double), installed, failed(String), unavailable
    }

    @Published private(set) var statuses: [OptionalComponent: Status] = [:]
    private var downloads: [OptionalComponent: Task<Void, Never>] = [:]
    private let directory = AppPaths.support.appendingPathComponent("OptionalModels", isDirectory: true)

    init() {
        for component in OptionalComponent.allCases {
            statuses[component] = component.asset == nil ? .unavailable : .checking
        }
        Task { await scan() }
    }

    func status(_ component: OptionalComponent) -> Status { statuses[component] ?? .checking }

    func scan() async {
        for component in OptionalComponent.allCases {
            guard let asset = component.asset, downloads[component] == nil else { continue }
            let file = destination(component, asset: asset)
            guard FileManager.default.fileExists(atPath: file.path) else {
                statuses[component] = .absent
                continue
            }
            do {
                // Existing files are not trusted by their name or length alone.
                let matches = try await verify(file, asset: asset)
                if downloads[component] == nil { statuses[component] = matches ? .installed : .failed(L("optional.error.integrity")) }
            } catch {
                if downloads[component] == nil { statuses[component] = .failed(error.localizedDescription) }
            }
        }
    }

    func install(_ component: OptionalComponent) {
        guard let asset = component.asset, asset.isValid, downloads[component] == nil else { return }
        if case .installed = status(component) { return }
        statuses[component] = .downloading(0)
        downloads[component] = Task { [weak self] in
            guard let self else { return }
            let target = destination(component, asset: asset)
            let temp = directory.appendingPathComponent(".\(component.id)-\(UUID().uuidString).download")
            defer {
                try? FileManager.default.removeItem(at: temp)
                downloads[component] = nil
            }
            do {
                try FileManager.default.createDirectory(at: directory, withIntermediateDirectories: true,
                                                        attributes: [.posixPermissions: 0o700])
                let downloader = ModelDownload(destination: temp, expectedBytes: asset.bytes) { [weak self] fraction in
                    Task { @MainActor in
                        guard let self, case .downloading = self.status(component) else { return }
                        self.statuses[component] = .downloading(min(1, max(0, fraction ?? 0)))
                    }
                }
                try await downloader.fetch(asset.url)
                try Task.checkCancellation()
                guard try await verify(temp, asset: asset) else {
                    throw AppFailure(L("optional.error.integrity"))
                }
                try Task.checkCancellation()
                // Only replace this optional asset, never other files or the main ASR model.
                if FileManager.default.fileExists(atPath: target.path) {
                    try FileManager.default.removeItem(at: target)
                }
                try FileManager.default.moveItem(at: temp, to: target)
                try FileManager.default.setAttributes([.posixPermissions: 0o600], ofItemAtPath: target.path)
                statuses[component] = .installed
            } catch {
                statuses[component] = Task.isCancelled ? .absent : .failed(error.localizedDescription)
            }
        }
    }

    func cancel(_ component: OptionalComponent) {
        downloads[component]?.cancel()
    }

    /// Removal is confined to one known filename in the app's optional directory.
    /// Optional assets have no live inference consumers in this patch.
    func remove(_ component: OptionalComponent) {
        guard let asset = component.asset, downloads[component] == nil else { return }
        do {
            let file = destination(component, asset: asset)
            if FileManager.default.fileExists(atPath: file.path) { try FileManager.default.removeItem(at: file) }
            statuses[component] = .absent
        } catch { statuses[component] = .failed(error.localizedDescription) }
    }

    private func destination(_ component: OptionalComponent, asset: OptionalAssetSpec) -> URL {
        directory.appendingPathComponent(component.id + "-" + asset.fileName)
    }

    private func verify(_ url: URL, asset: OptionalAssetSpec) async throws -> Bool {
        try await Task.detached(priority: .utility) {
            let attrs = try FileManager.default.attributesOfItem(atPath: url.path)
            guard (attrs[.size] as? NSNumber)?.int64Value == asset.bytes else { return false }
            let file = try FileHandle(forReadingFrom: url)
            defer { try? file.close() }
            var sha = SHA256()
            while true {
                try Task.checkCancellation()
                let data = try file.read(upToCount: 256 * 1024) ?? Data()
                if data.isEmpty { break }
                sha.update(data: data)
            }
            let actual = sha.finalize().map { String(format: "%02x", $0) }.joined()
            return actual == asset.sha256
        }.value
    }
}
#endif
