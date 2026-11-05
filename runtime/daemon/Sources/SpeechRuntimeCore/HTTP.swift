import Foundation
import Network

public struct HTTPRequest: Sendable {
    public let method: String
    public let path: String
    public let query: [String: String]
    public let headers: [String: String]
    public let body: Data
}

public struct HTTPResponse: Sendable {
    public var status: Int
    public var headers: [String: String]
    public var body: Data

    public init(status: Int = 200, headers: [String: String] = [:], body: Data = Data()) {
        self.status = status
        self.headers = headers
        self.body = body
    }

    public static func json(_ value: Any, status: Int = 200) -> HTTPResponse {
        let data = (try? JSONSerialization.data(withJSONObject: value, options: [.withoutEscapingSlashes]))
            ?? Data("{\"error\":\"encoding\"}".utf8)
        return HTTPResponse(status: status, headers: ["Content-Type": "application/json"], body: data)
    }

    public static func error(_ message: String, status: Int) -> HTTPResponse {
        .json(["error": message], status: status)
    }
}

/// Minimal HTTP/1.1 server on loopback: one request per connection, `Content-Length` bodies.
///
/// Loopback clients are few (one per app) and requests are small except audio uploads, so a
/// dependency-free parser is enough and keeps the daemon a single small binary.
public final class HTTPServer: @unchecked Sendable {
    private let listener: NWListener
    private let handler: @Sendable (HTTPRequest) async -> HTTPResponse
    private let queue = DispatchQueue(label: "speech-runtime.http", attributes: .concurrent)

    public init(port: UInt16, handler: @escaping @Sendable (HTTPRequest) async -> HTTPResponse) throws {
        let params = NWParameters.tcp
        params.allowLocalEndpointReuse = true
        params.requiredLocalEndpoint = NWEndpoint.hostPort(host: "127.0.0.1", port: NWEndpoint.Port(rawValue: port)!)
        self.listener = try NWListener(using: params)
        self.handler = handler
    }

    /// Hard limits for one loopback request. Clients are few and trusted-ish, but a malformed or
    /// hostile one must not make the daemon allocate without bound or hold a socket open forever.
    enum Limits {
        static let headerBytes = 32 * 1024
        static let headerCount = 100
        static let bodyBytes = 256 * 1024 * 1024
        static let requestTimeout: TimeInterval = 30
    }

    public func start() {
        listener.newConnectionHandler = { [weak self] connection in
            guard let self else { return }
            connection.start(queue: self.queue)
            // A client that opened a socket and stalled would otherwise pin the connection (and
            // its buffer) until the peer goes away. `cancel` on a finished connection is a no-op.
            self.queue.asyncAfter(deadline: .now() + Limits.requestTimeout) {
                connection.cancel()
            }
            self.receive(on: connection, buffer: Data())
        }
        listener.start(queue: queue)
    }

    public func stop() {
        listener.cancel()
    }

    private func receive(on connection: NWConnection, buffer: Data) {
        connection.receive(minimumIncompleteLength: 1, maximumLength: 1 << 20) { [weak self] data, _, isComplete, error in
            guard let self else { return }
            var buffer = buffer
            if let data { buffer.append(data) }

            switch Self.parse(buffer) {
            case .ready(let parsed) where parsed.request.body.count >= parsed.contentLength:
                let request = parsed.request
                Task {
                    let response = await self.handler(request)
                    self.write(response, to: connection)
                }
                return
            case .rejected(let failure):
                // Reject before the body arrives: an oversized or malformed declaration is enough.
                self.write(.error(failure.message, status: failure.status), to: connection)
                return
            default:
                break
            }
            if isComplete || error != nil {
                connection.cancel()
                return
            }
            self.receive(on: connection, buffer: buffer)
        }
    }

    private func write(_ response: HTTPResponse, to connection: NWConnection) {
        var head = "HTTP/1.1 \(response.status) \(Self.reason(response.status))\r\n"
        head += "Content-Length: \(response.body.count)\r\n"
        head += "Connection: close\r\n"
        for (key, value) in response.headers {
            head += "\(key): \(value)\r\n"
        }
        head += "\r\n"
        var data = Data(head.utf8)
        data.append(response.body)
        connection.send(content: data, completion: .contentProcessed { _ in
            connection.cancel()
        })
    }

    private static func reason(_ status: Int) -> String {
        switch status {
        case 200: return "OK"
        case 202: return "Accepted"
        case 204: return "No Content"
        case 400: return "Bad Request"
        case 401: return "Unauthorized"
        case 404: return "Not Found"
        case 409: return "Conflict"
        case 413: return "Payload Too Large"
        case 431: return "Request Header Fields Too Large"
        case 500: return "Internal Server Error"
        case 503: return "Service Unavailable"
        default: return "Status"
        }
    }

    /// Parser verdicts. `incomplete` means "read more bytes"; the daemon never guesses a default
    /// for a value the client declared, and never dispatches a request it could not fully frame.
    enum ParseOutcome {
        case incomplete
        case ready(Parsed)
        case rejected(ParseFailure)
    }

    /// Framing errors the client can be told about. They are answered before the body is read.
    enum ParseFailure: Error, Equatable {
        case malformedRequestLine
        case malformedHeader
        case duplicateContentLength
        case invalidContentLength
        case unsupportedTransferEncoding
        case headersTooLarge
        case tooManyHeaders
        case bodyTooLarge

        var status: Int {
            switch self {
            case .unsupportedTransferEncoding: return 501
            case .bodyTooLarge: return 413
            case .headersTooLarge, .tooManyHeaders: return 431
            default: return 400
            }
        }

        var message: String {
            switch self {
            case .unsupportedTransferEncoding: return "transfer-encoding is not supported"
            case .bodyTooLarge: return "request body too large"
            case .headersTooLarge: return "request headers too large"
            case .tooManyHeaders: return "too many headers"
            case .duplicateContentLength: return "conflicting content-length headers"
            case .invalidContentLength: return "invalid content-length header"
            case .malformedHeader: return "malformed header line"
            case .malformedRequestLine: return "malformed request line"
            }
        }
    }

    struct Parsed {
        var request: HTTPRequest
        var contentLength: Int
    }

    static func parse(_ buffer: Data) -> ParseOutcome {
        let separator = Data([13, 10, 13, 10])
        guard let range = buffer.range(of: separator) else {
            // No header terminator yet: bounded so a client cannot grow the buffer forever.
            return buffer.count > Limits.headerBytes ? .rejected(.headersTooLarge) : .incomplete
        }
        // A block that arrives *with* its terminator in one burst skips the unterminated-prefix
        // check below, so the limit has to be applied to the framed head as well; otherwise an
        // oversized header block is parsed and dispatched instead of answered with 431.
        if buffer.distance(from: buffer.startIndex, to: range.lowerBound) > Limits.headerBytes {
            return .rejected(.headersTooLarge)
        }
        let headData = buffer[buffer.startIndex..<range.lowerBound]
        guard let head = String(data: headData, encoding: .utf8) else {
            return .rejected(.malformedRequestLine)
        }
        var lines = head.components(separatedBy: "\r\n")
        guard let requestLine = lines.first, !requestLine.isEmpty else {
            return .rejected(.malformedRequestLine)
        }
        lines.removeFirst()
        let parts = requestLine.split(separator: " ")
        guard parts.count >= 2 else { return .rejected(.malformedRequestLine) }
        if parts.count >= 3, !parts[2].hasPrefix("HTTP/1.") {
            return .rejected(.malformedRequestLine)
        }
        let method = String(parts[0])
        let target = String(parts[1])
        let split = target.split(separator: "?", maxSplits: 1, omittingEmptySubsequences: false)
        let path = String(split[0])
        var query: [String: String] = [:]
        if split.count > 1 {
            for pair in split[1].split(separator: "&") {
                let kv = pair.split(separator: "=", maxSplits: 1, omittingEmptySubsequences: false)
                let key = String(kv[0]).removingPercentEncoding ?? String(kv[0])
                let value = kv.count > 1 ? (String(kv[1]).removingPercentEncoding ?? String(kv[1])) : ""
                query[key] = value
            }
        }
        guard lines.count <= Limits.headerCount else { return .rejected(.tooManyHeaders) }

        var headers: [String: String] = [:]
        var contentLength: Int?
        for line in lines where !line.isEmpty {
            // Obsolete line folding: continuation lines are ambiguous with the terminator.
            if line.hasPrefix(" ") || line.hasPrefix("\t") { return .rejected(.malformedHeader) }
            guard let colon = line.firstIndex(of: ":") else { return .rejected(.malformedHeader) }
            let key = line[..<colon].trimmingCharacters(in: .whitespaces).lowercased()
            let isToken = !key.isEmpty && key.allSatisfy { char in
                char.isASCII && (char.isLetter || char.isNumber || "-_".contains(char))
            }
            guard isToken else { return .rejected(.malformedHeader) }
            let value = line[line.index(after: colon)...].trimmingCharacters(in: .whitespaces)
            // Chunked framing would need a different body reader; refuse instead of misreading it.
            if key == "transfer-encoding" { return .rejected(.unsupportedTransferEncoding) }
            if key == "content-length" {
                // Digits only: `Int("+5")` and `Int(" 5")` would otherwise be accepted silently.
                let digits = !value.isEmpty && value.allSatisfy { $0.isASCII && $0.isNumber }
                guard digits, let declared = Int(value) else { return .rejected(.invalidContentLength) }
                if let existing = contentLength, existing != declared {
                    return .rejected(.duplicateContentLength)
                }
                contentLength = declared
            }
            headers[key] = value
        }
        let length = contentLength ?? 0
        guard length <= Limits.bodyBytes else { return .rejected(.bodyTooLarge) }
        let bodyStart = range.upperBound
        let available = buffer.distance(from: bodyStart, to: buffer.endIndex)
        let body = available >= length ? buffer[bodyStart..<buffer.index(bodyStart, offsetBy: length)] : Data()
        return .ready(Parsed(
            request: HTTPRequest(method: method, path: path, query: query, headers: headers, body: Data(body)),
            contentLength: length
        ))
    }
}
