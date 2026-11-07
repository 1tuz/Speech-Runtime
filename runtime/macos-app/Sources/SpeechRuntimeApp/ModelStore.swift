#if os(macOS)
import CryptoKit
import Foundation
import RuntimeUIModel

@MainActor
final class ModelStore {
    struct Receipt: Codable {
        var revision: String
        var sha256: String
        var size: Int64
        var modification: Double
    }
    struct RemoteEntry: Decodable {
        let path: String
        let size: Int64?
        let lfs: LFS?
        struct LFS: Decodable { let oid: String; let size: Int64? }
    }
    var isInstalled: Bool { Self.entryExists(AppPaths.model.path) }

    /// `fileExists` follows symlinks: a broken link (left by copying the HF cache
    /// layout, whose snapshot files are relative symlinks to blobs/) reports false
    /// yet still blocks `copyItem`/`moveItem` with "object already exists".
    static func entryExists(_ path: String) -> Bool {
        // lstat needs a real stat buffer; passing nil fails with EFAULT even
        // for existing files (found live: entryExists was always false).
        var st = stat()
        return lstat(path, &st) == 0
    }

    func ensureInstalled(progress: @escaping @Sendable (Double?) -> Void) async throws {
        try AppPaths.createDirectories()
        if try cachedReceiptIsValid() { return }
        // Metadata and media are pinned to the same immutable HF commit.
        // Never trust a filename or a locally calculated hash as publisher verification.
        let (metadata, response) = try await URLSession.shared.data(from: ModelSpec.metadataURL)
        guard (response as? HTTPURLResponse)?.statusCode == 200 else { throw AppFailure(L("error.metadata")) }
        let files = try JSONDecoder().decode([RemoteEntry].self, from: metadata)
        guard let file = files.first(where: { $0.path == ModelSpec.filename }),
              let digest = file.lfs?.oid.lowercased(), digest.count == 64,
              digest.allSatisfy({ $0.isHexDigit }),
              (file.lfs?.size ?? file.size) == ModelSpec.expectedBytes else {
            throw AppFailure(L("error.metadata"))
        }

        let cached = FileManager.default.homeDirectoryForCurrentUser
            .appendingPathComponent(".cache/huggingface/hub/models--handy-computer--parakeet-ultra-gguf/snapshots")
            .appendingPathComponent(ModelSpec.revision).appendingPathComponent(ModelSpec.filename)
        // The HF snapshot entry is a relative symlink into blobs/; resolve it so the
        // copy brings real bytes, not a link that is broken outside the cache.
        let cachedResolved = (try? FileManager.default.destinationOfSymbolicLink(atPath: cached.path))
            .map { link in URL(fileURLWithPath: link, relativeTo: cached.deletingLastPathComponent()) }
            ?? cached
        if !isInstalled, FileManager.default.fileExists(atPath: cachedResolved.path) {
            progress(nil)
            try FileManager.default.copyItem(at: cachedResolved, to: AppPaths.model)
        }
        if isInstalled {
            progress(nil)
            if try await verify(AppPaths.model, expectedHash: digest) {
                try saveReceipt(hash: digest)
                return
            }
            // An app-managed file that failed verification is never silently used.
            // removeItem also clears a stale/broken symlink from an earlier attempt.
            try FileManager.default.removeItem(at: AppPaths.model)
        }

        let temporary = AppPaths.modelDirectory.appendingPathComponent(UUID().uuidString + ".download")
        defer { try? FileManager.default.removeItem(at: temporary) }
        let downloader = ModelDownload(destination: temporary, progress: progress)
        try await downloader.fetch(ModelSpec.fileURL)
        try Task.checkCancellation()
        progress(nil)
        guard try await verify(temporary, expectedHash: digest) else { throw AppFailure(L("error.integrity")) }
        try FileManager.default.moveItem(at: temporary, to: AppPaths.model)
        try saveReceipt(hash: digest)
    }

    func prepare() async throws {
        let helper = AppPaths.executable("sr-model-prepare")
        guard FileManager.default.isExecutableFile(atPath: helper.path) else { throw AppFailure(L("error.bundle") + " sr-model-prepare") }
        let result = try await Command.run(helper, [AppPaths.model.path], timeout: 600)
        try Task.checkCancellation()
        guard result.status == 0 else { throw AppFailure(L("error.prepare") + "\n" + result.output) }
    }

    private func cachedReceiptIsValid() throws -> Bool {
        guard let data = try? Data(contentsOf: AppPaths.receipt),
              let receipt = try? JSONDecoder().decode(Receipt.self, from: data), isInstalled else { return false }
        let attributes = try FileManager.default.attributesOfItem(atPath: AppPaths.model.path)
        let size = (attributes[.size] as? NSNumber)?.int64Value
        let date = (attributes[.modificationDate] as? Date)?.timeIntervalSince1970
        return receipt.revision == ModelSpec.revision && receipt.sha256.count == 64 &&
            receipt.size == ModelSpec.expectedBytes && size == receipt.size && date == receipt.modification
    }
    private func saveReceipt(hash: String) throws {
        let attributes = try FileManager.default.attributesOfItem(atPath: AppPaths.model.path)
        guard let date = attributes[.modificationDate] as? Date else { throw AppFailure(L("error.integrity")) }
        let receipt = Receipt(revision: ModelSpec.revision, sha256: hash, size: ModelSpec.expectedBytes,
            modification: date.timeIntervalSince1970)
        try AppPaths.writePrivate(JSONEncoder().encode(receipt), to: AppPaths.receipt)
    }
    private func verify(_ file: URL, expectedHash: String) async throws -> Bool {
        let task = Task.detached(priority: .utility) {
            let attributes = try FileManager.default.attributesOfItem(atPath: file.path)
            guard (attributes[.size] as? NSNumber)?.int64Value == ModelSpec.expectedBytes else { return false }
            let handle = try FileHandle(forReadingFrom: file)
            defer { try? handle.close() }
            var hash = SHA256()
            while true {
                try Task.checkCancellation()
                let data = try autoreleasepool { try handle.read(upToCount: 1 << 20) ?? Data() }
                if data.isEmpty { break }
                hash.update(data: data)
            }
            return hash.finalize().map { String(format: "%02x", $0) }.joined() == expectedHash
        }
        return try await withTaskCancellationHandler { try await task.value } onCancel: { task.cancel() }
    }
}

/// Streaming download: no 740 MB Data buffer. Serial delegate callbacks, exactly one continuation.
private final class ModelDownload: NSObject, URLSessionDownloadDelegate, @unchecked Sendable {
    private let lock = NSLock()
    private var continuation: CheckedContinuation<Void, Error>?
    private var session: URLSession?
    private var cancelled = false
    private let destination: URL
    private let progress: @Sendable (Double?) -> Void
    init(destination: URL, progress: @escaping @Sendable (Double?) -> Void) {
        self.destination = destination; self.progress = progress
    }
    func fetch(_ url: URL) async throws {
        try await withTaskCancellationHandler {
            try await startDownload(url)
        } onCancel: {
            self.cancelDownload()
        }
    }
    private func startDownload(_ url: URL) async throws {
        try await withCheckedThrowingContinuation { (continuation: CheckedContinuation<Void, Error>) in
            lock.lock()
            if cancelled { lock.unlock(); continuation.resume(throwing: CancellationError()); return }
            self.continuation = continuation
            let config = URLSessionConfiguration.ephemeral
            config.timeoutIntervalForRequest = 60
            config.timeoutIntervalForResource = 3600
            let queue = OperationQueue(); queue.maxConcurrentOperationCount = 1
            let session = URLSession(configuration: config, delegate: self, delegateQueue: queue)
            self.session = session
            lock.unlock()
            session.downloadTask(with: url).resume()
        }
    }
    private func cancelDownload() {
        lock.lock(); cancelled = true; let session = self.session; lock.unlock()
        session?.invalidateAndCancel()
    }
    func urlSession(_ session: URLSession, downloadTask: URLSessionDownloadTask,
                    didWriteData bytesWritten: Int64, totalBytesWritten: Int64, totalBytesExpectedToWrite: Int64) {
        progress(Double(totalBytesWritten) / Double(ModelSpec.expectedBytes))
    }
    func urlSession(_ session: URLSession, downloadTask: URLSessionDownloadTask, didFinishDownloadingTo location: URL) {
        do {
            guard (downloadTask.response as? HTTPURLResponse)?.statusCode == 200 else {
                throw AppFailure(L("error.download"))
            }
            try FileManager.default.moveItem(at: location, to: destination)
            finish(.success(()))
        } catch { finish(.failure(error)) }
    }
    func urlSession(_ session: URLSession, task: URLSessionTask, didCompleteWithError error: Error?) {
        if let error { finish(.failure(error)) }
    }
    private func finish(_ result: Result<Void, Error>) {
        lock.lock()
        let continuation = self.continuation; self.continuation = nil
        let session = self.session; self.session = nil
        lock.unlock()
        session?.finishTasksAndInvalidate()
        continuation?.resume(with: result)
    }
}
#endif
