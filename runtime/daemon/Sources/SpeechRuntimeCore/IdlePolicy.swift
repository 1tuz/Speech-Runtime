import Foundation

/// Tracks model use so the daemon can drop weights that no client has touched for a while.
/// Loading is expensive (ANE compilation), so the daemon unloads lazily and never during a stream.
public actor IdlePolicy {
    private var lastUse: [String: Date] = [:]
    private var activeStreams: [String: (model: String, generation: Int)] = [:]

    public init() {}

    public func touch(_ model: String) {
        lastUse[model] = Date()
    }

    public func streamOpened(_ id: String, model: String, generation: Int) {
        activeStreams[id] = (model, generation)
        lastUse[model] = Date()
    }

    public func streamClosed(_ id: String) {
        activeStreams.removeValue(forKey: id)
    }

    /// Streams opened against a dead worker can never close; drop them so they stop blocking unload.
    public func pruneStreams(before generation: Int) {
        activeStreams = activeStreams.filter { $0.value.generation >= generation }
    }

    public func modelsToUnload(idle: TimeInterval, probe: @Sendable (String) async -> Bool) async -> [String] {
        let busy = Set(activeStreams.values.map(\.model))
        var candidates: [String] = []
        for (model, used) in lastUse where Date().timeIntervalSince(used) >= idle {
            if busy.contains(model) { continue }
            if await probe(model) { candidates.append(model) }
        }
        return candidates
    }

    public func didUnload(_ model: String) {
        lastUse.removeValue(forKey: model)
    }
}
