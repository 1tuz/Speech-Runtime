import Foundation
import XCTest
@testable import SpeechRuntimeCore

final class HTTPTests: XCTestCase {
    private func parsed(_ raw: Data, file: StaticString = #filePath, line: UInt = #line) throws -> HTTPServer.Parsed {
        switch HTTPServer.parse(raw) {
        case .ready(let parsed): return parsed
        case .incomplete: XCTFail("expected a complete request", file: file, line: line)
        case .rejected(let failure): XCTFail("expected a complete request, got \(failure)", file: file, line: line)
        }
        throw XCTSkip("unreachable")
    }

    private func rejected(_ raw: Data, file: StaticString = #filePath, line: UInt = #line) -> HTTPServer.ParseFailure? {
        switch HTTPServer.parse(raw) {
        case .rejected(let failure): return failure
        case .ready: XCTFail("expected rejection", file: file, line: line)
        case .incomplete: XCTFail("expected rejection, got incomplete", file: file, line: line)
        }
        return nil
    }

    func testParsesRequestLineQueryAndBody() throws {
        var raw = Data("POST /v1/streams/audio?stream=abc&lang=ru HTTP/1.1\r\nHost: 127.0.0.1\r\nAuthorization: Bearer t\r\nContent-Length: 4\r\n\r\n".utf8)
        raw.append(Data([1, 2, 3, 4]))
        let parsed = try parsed(raw)
        XCTAssertEqual(parsed.request.method, "POST")
        XCTAssertEqual(parsed.request.path, "/v1/streams/audio")
        XCTAssertEqual(parsed.request.query["stream"], "abc")
        XCTAssertEqual(parsed.request.query["lang"], "ru")
        XCTAssertEqual(parsed.request.headers["authorization"], "Bearer t")
        XCTAssertEqual(parsed.request.body, Data([1, 2, 3, 4]))
    }

    func testWaitsForCompleteBody() throws {
        let partial = Data("POST /x HTTP/1.1\r\nContent-Length: 10\r\n\r\nshort".utf8)
        guard case .ready(let parsed) = HTTPServer.parse(partial) else {
            return XCTFail("header block is complete; the body is not")
        }
        XCTAssertEqual(parsed.contentLength, 10)
        XCTAssertEqual(parsed.request.body.count, 0, "body is only complete when Content-Length bytes arrived")
    }

    func testEmptyQueryValueDoesNotCrashOrDropTheKey() throws {
        let parsed = try parsed(Data("GET /v1/streams/events?stream=&wait= HTTP/1.1\r\n\r\n".utf8))
        XCTAssertEqual(parsed.request.query["stream"], "")
        XCTAssertEqual(parsed.request.query["wait"], "")
    }

    func testRejectsNegativeContentLength() {
        // `Int("-1")` used to be accepted, and a negative length reached a slice offset.
        XCTAssertEqual(rejected(Data("POST /x HTTP/1.1\r\nContent-Length: -1\r\n\r\n".utf8)), .invalidContentLength)
    }

    func testRejectsNonNumericContentLength() {
        XCTAssertEqual(rejected(Data("POST /x HTTP/1.1\r\nContent-Length: 5x\r\n\r\n".utf8)), .invalidContentLength)
        XCTAssertEqual(rejected(Data("POST /x HTTP/1.1\r\nContent-Length: +5\r\n\r\n".utf8)), .invalidContentLength)
        XCTAssertEqual(rejected(Data("POST /x HTTP/1.1\r\nContent-Length:\r\n\r\n".utf8)), .invalidContentLength)
    }

    func testAcceptsRepeatedEqualContentLength() throws {
        let parsed = try parsed(Data("POST /x HTTP/1.1\r\nContent-Length: 2\r\nContent-Length: 2\r\n\r\n".utf8))
        XCTAssertEqual(parsed.contentLength, 2)
    }

    func testRejectsConflictingContentLength() {
        let raw = Data("POST /x HTTP/1.1\r\nContent-Length: 2\r\nContent-Length: 3\r\n\r\n".utf8)
        XCTAssertEqual(rejected(raw), .duplicateContentLength)
    }

    func testRejectsTransferEncoding() {
        let raw = Data("POST /x HTTP/1.1\r\nTransfer-Encoding: chunked\r\n\r\n".utf8)
        XCTAssertEqual(rejected(raw), .unsupportedTransferEncoding)
        XCTAssertEqual(rejected(raw)?.status, 501)
    }

    func testRejectsOversizedDeclaredBody() {
        let raw = Data("POST /x HTTP/1.1\r\nContent-Length: \(HTTPServer.Limits.bodyBytes + 1)\r\n\r\n".utf8)
        XCTAssertEqual(rejected(raw), .bodyTooLarge)
        XCTAssertEqual(rejected(raw)?.status, 413)
    }

    func testRejectsTooManyHeaders() {
        var raw = Data("POST /x HTTP/1.1\r\n".utf8)
        for index in 0...HTTPServer.Limits.headerCount { raw.append(Data("X-\(index): 1\r\n".utf8)) }
        raw.append(Data("\r\n".utf8))
        XCTAssertEqual(rejected(raw), .tooManyHeaders)
    }

    func testRejectsHeaderBlockWithoutTerminatorPastLimit() {
        let raw = Data(repeating: 0x41, count: HTTPServer.Limits.headerBytes + 1)
        XCTAssertEqual(rejected(raw), .headersTooLarge)
        XCTAssertEqual(rejected(raw)?.status, 431)
    }

    /// Same limit, but the oversized block arrives together with its terminator: the
    /// unterminated-prefix check never runs, so the framed head has to be measured too.
    func testRejectsTerminatedHeaderBlockPastLimit() {
        let padding = String(repeating: "a", count: HTTPServer.Limits.headerBytes)
        let raw = Data("POST /x HTTP/1.1\r\nX-Pad: \(padding)\r\n\r\n".utf8)
        XCTAssertEqual(rejected(raw), .headersTooLarge)
        XCTAssertEqual(rejected(raw)?.status, 431)
    }

    /// The limit is a ceiling, not a boundary that rejects a legal maximal request.
    func testAcceptsHeaderBlockAtLimit() throws {
        let overhead = "POST /x HTTP/1.1\r\nX-Pad: \r\n\r\n".utf8.count
        let padding = String(repeating: "a", count: HTTPServer.Limits.headerBytes - overhead)
        let raw = Data("POST /x HTTP/1.1\r\nX-Pad: \(padding)\r\n\r\n".utf8)
        XCTAssertEqual(raw.count, HTTPServer.Limits.headerBytes)
        let request = try parsed(raw).request
        XCTAssertEqual(request.path, "/x")
        XCTAssertEqual(request.headers["x-pad"]?.count, padding.count)
    }

    func testRejectsFoldedHeaderLine() {
        XCTAssertEqual(rejected(Data("POST /x HTTP/1.1\r\nX-A: 1\r\n  folded\r\n\r\n".utf8)), .malformedHeader)
    }

    func testRejectsHeaderLineWithoutColon() {
        XCTAssertEqual(rejected(Data("POST /x HTTP/1.1\r\nnonsense\r\n\r\n".utf8)), .malformedHeader)
    }

    func testRejectsNonHTTP1VersionAndEmptyRequestLine() {
        XCTAssertEqual(rejected(Data("POST /x HTTP/2.0\r\n\r\n".utf8)), .malformedRequestLine)
        XCTAssertEqual(rejected(Data("GET\r\n\r\n".utf8)), .malformedRequestLine)
    }

    func testTokenComparisonRejectsWrongToken() {
        let token = AuthToken(value: "secret-token-value")
        XCTAssertTrue(token.matches("Bearer secret-token-value"))
        XCTAssertFalse(token.matches("Bearer secret-token-valuE"))
        XCTAssertFalse(token.matches("Bearer short"))
        XCTAssertFalse(token.matches(nil))
        XCTAssertFalse(token.matches("secret-token-value"))
    }
}

final class AudioTests: XCTestCase {
    func testDecodes16BitMonoWAV() throws {
        let samples: [Int16] = [0, 16_384, -16_384, 32_767]
        let data = makeWAV(samples: samples, channels: 1, rate: 16_000, bits: 16)
        let decoded = try Audio.decode(body: data, rawFormat: nil)
        XCTAssertEqual(decoded.sampleRate, 16_000)
        XCTAssertEqual(decoded.samples.count, 4)
        XCTAssertEqual(decoded.samples[1], 0.5, accuracy: 1e-4)
        XCTAssertEqual(decoded.samples[2], -0.5, accuracy: 1e-4)
    }

    func testDownmixesStereoByAveraging() throws {
        // left = 1.0, right = 0.0
        var body = Data()
        body.append(Data([0x00, 0x40, 0x00, 0x00])) // 16384, 0
        let data = makeWAV(payload: body, channels: 2, rate: 16_000, bits: 16)
        let decoded = try Audio.decode(body: data, rawFormat: nil)
        XCTAssertEqual(decoded.samples.count, 1)
        XCTAssertEqual(decoded.samples[0], 0.25, accuracy: 1e-4)
    }

    func testRejectsNonWAVWithoutFormatHeader() {
        XCTAssertThrowsError(try Audio.decode(body: Data([0, 1, 2, 3]), rawFormat: nil))
    }

    func testDecodesRawS16LE() throws {
        var body = Data()
        for value in [Int16(0), 16_384] { body.append(contentsOf: withUnsafeBytes(of: value.littleEndian) { Array($0) }) }
        let decoded = try Audio.decode(body: body, rawFormat: "s16le")
        XCTAssertEqual(decoded.samples, [0, 0.5])
    }

    private func makeWAV(samples: [Int16], channels: UInt16, rate: UInt32, bits: UInt16) -> Data {
        var payload = Data()
        for value in samples { payload.append(contentsOf: withUnsafeBytes(of: value.littleEndian) { Array($0) }) }
        return makeWAV(payload: payload, channels: channels, rate: rate, bits: bits)
    }

    private func makeWAV(payload: Data, channels: UInt16, rate: UInt32, bits: UInt16) -> Data {
        var out = Data("RIFF".utf8)
        out.append(contentsOf: withUnsafeBytes(of: UInt32(36 + payload.count).littleEndian) { Array($0) })
        out.append(Data("WAVEfmt ".utf8))
        out.append(contentsOf: withUnsafeBytes(of: UInt32(16).littleEndian) { Array($0) })
        out.append(contentsOf: withUnsafeBytes(of: UInt16(1).littleEndian) { Array($0) })
        out.append(contentsOf: withUnsafeBytes(of: channels.littleEndian) { Array($0) })
        out.append(contentsOf: withUnsafeBytes(of: rate.littleEndian) { Array($0) })
        out.append(contentsOf: withUnsafeBytes(of: (rate * UInt32(channels) * UInt32(bits / 8)).littleEndian) { Array($0) })
        out.append(contentsOf: withUnsafeBytes(of: (channels * bits / 8).littleEndian) { Array($0) })
        out.append(contentsOf: withUnsafeBytes(of: bits.littleEndian) { Array($0) })
        out.append(Data("data".utf8))
        out.append(contentsOf: withUnsafeBytes(of: UInt32(payload.count).littleEndian) { Array($0) })
        out.append(payload)
        return out
    }
}
