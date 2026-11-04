import Foundation

/// Thin client used by `sr` and by tests. It only speaks the loopback HTTP API, exactly like any
/// third-party app would, so a passing CLI run is evidence about the API and not about internals.
public struct RuntimeClient: Sendable {
    public var baseURL: URL
    public var token: String
    private let session: URLSession

    public init(baseURL: URL, token: String) {
        self.baseURL = baseURL
        self.token = token
        let config = URLSessionConfiguration.ephemeral
        config.timeoutIntervalForRequest = 600
        config.timeoutIntervalForResource = 900
        self.session = URLSession(configuration: config)
    }

    public static func local() throws -> RuntimeClient {
        let token = try String(contentsOf: Paths.token, encoding: .utf8).trimmingCharacters(in: .whitespacesAndNewlines)
        return RuntimeClient(baseURL: URL(string: "http://127.0.0.1:8477")!, token: token)
    }

    public enum Failure: Error, CustomStringConvertible {
        case http(Int, String)
        case transport(String)

        public var description: String {
            switch self {
            case .http(let status, let message): return "HTTP \(status): \(message)"
            case .transport(let message): return message
            }
        }
    }

    private func send(
        _ method: String,
        _ path: String,
        query: [String: String] = [:],
        body: Data? = nil,
        contentType: String = "application/json",
        headers: [String: String] = [:]
    ) async throws -> [String: Any] {
        var components = URLComponents(url: baseURL.appendingPathComponent(path), resolvingAgainstBaseURL: false)!
        if !query.isEmpty {
            components.queryItems = query.map { URLQueryItem(name: $0.key, value: $0.value) }
        }
        var request = URLRequest(url: components.url!)
        request.httpMethod = method
        request.setValue("Bearer \(token)", forHTTPHeaderField: "Authorization")
        if let body {
            request.httpBody = body
            request.setValue(contentType, forHTTPHeaderField: "Content-Type")
        }
        for (name, value) in headers {
            request.setValue(value, forHTTPHeaderField: name)
        }
        do {
            let (data, response) = try await session.data(for: request)
            let status = (response as? HTTPURLResponse)?.statusCode ?? 0
            let object = (try? JSONSerialization.jsonObject(with: data)) as? [String: Any] ?? [:]
            guard status / 100 == 2 else {
                throw Failure.http(status, object["error"] as? String ?? String(data: data, encoding: .utf8) ?? "")
            }
            return object
        } catch let error as Failure {
            throw error
        } catch {
            throw Failure.transport("cannot reach \(baseURL.absoluteString): \(error.localizedDescription)")
        }
    }

    public func health() async throws -> [String: Any] {
        try await send("GET", "/health")
    }

    public func models() async throws -> [String: Any] {
        try await send("GET", "/v1/models")
    }

    public func load(model: String, backend: String? = nil) async throws -> [String: Any] {
        var body: [String: Any] = ["model": model]
        if let backend { body["backend"] = backend }
        return try await send("POST", "/v1/models/load", body: try JSONSerialization.data(withJSONObject: body))
    }

    public func unload(model: String) async throws -> [String: Any] {
        try await send("POST", "/v1/models/unload", body: try JSONSerialization.data(withJSONObject: ["model": model]))
    }

    public func transcribe(
        wav: Data,
        model: String,
        language: String? = nil,
        windowMs: Int? = nil,
        priority: String = "batch"
    ) async throws -> [String: Any] {
        var query = ["model": model, "priority": priority]
        if let language { query["language"] = language }
        if let windowMs { query["window_ms"] = String(windowMs) }
        return try await send("POST", "/v1/audio/transcriptions", query: query, body: wav, contentType: "audio/wav")
    }

    public func openStream(model: String, config: [String: Any] = [:]) async throws -> [String: Any] {
        var body: [String: Any] = ["model": model, "config": config]
        if let language = config["language"] { body["language"] = language }
        return try await send("POST", "/v1/streams", body: try JSONSerialization.data(withJSONObject: body))
    }

    public func streamAudio(stream: String, pcm: [Float]) async throws -> [String: Any] {
        let body = LocalPCM.encode(pcm)
        return try await send(
            "POST", "/v1/streams/audio", query: ["stream": stream],
            body: body, contentType: "application/octet-stream",
            headers: ["X-Sample-Format": "f32le", "X-Sample-Rate": "16000"]
        )
    }

    public func endStream(_ id: String) async throws -> [String: Any] {
        try await send("POST", "/v1/streams/end", query: ["stream": id])
    }

    public func events(stream: String, since: Int, wait: Double) async throws -> [String: Any] {
        try await send("GET", "/v1/streams/events", query: [
            "stream": stream, "since": String(since), "wait": String(format: "%.2f", wait),
        ])
    }

    public func cancelStream(_ id: String) async throws -> [String: Any] {
        try await send("DELETE", "/v1/streams", query: ["stream": id])
    }
}

public enum LocalPCM {
    public static func encode(_ samples: [Float]) -> Data {
        var data = Data(capacity: samples.count * 4)
        for sample in samples {
            var bits = sample.bitPattern.littleEndian
            withUnsafeBytes(of: &bits) { data.append(contentsOf: $0) }
        }
        return data
    }

    /// Decodes 16-bit PCM WAV into 16 kHz mono floats if the file is already 16 kHz.
    public static func decodeWAV(_ data: Data) throws -> (samples: [Float], rate: Double) {
        let decoded = try Audio.decodeWAV(data)
        return (decoded.samples, decoded.sampleRate)
    }
}

/// Stage-separated latency for one streaming final. Pure arithmetic, unit-tested directly:
/// `e2e` is wall time from feeding the audio position `last_voice_ms` to receipt of the final;
/// `silence` is the endpointer's silence window scaled by the feed speed; `delivery` is the
/// remainder after subtracting queue and ASR, clamped at zero (raw kept alongside).
public struct StreamLatency: Sendable, Equatable {
    public var e2eMs: Int
    public var silenceMs: Int
    public var queueMs: Int
    public var asrMs: Int
    /// `e2e - silence - queue - asr`, clamped to >= 0.
    public var deliveryMs: Int
    /// Same as `deliveryMs` without clamping; negative values mean the stages over-explain e2e.
    public var rawDeliveryMs: Int

    public static func stages(
        fed: Date,
        receipt: Date,
        silenceAudioMs: Double,
        queueMs: Int,
        asrMs: Int,
        speed: Double
    ) -> StreamLatency {
        let effectiveSpeed = speed > 0 ? speed : 1
        let e2e = max(receipt.timeIntervalSince(fed) * 1000, 0)
        let silence = silenceAudioMs / effectiveSpeed
        let rawDelivery = e2e - silence - Double(queueMs) - Double(asrMs)
        return StreamLatency(
            e2eMs: Int(e2e.rounded()),
            silenceMs: Int(silence.rounded()),
            queueMs: queueMs,
            asrMs: asrMs,
            deliveryMs: Int(max(rawDelivery, 0).rounded()),
            rawDeliveryMs: Int(rawDelivery.rounded())
        )
    }
}
