import Foundation
import XCTest
@testable import SpeechRuntimeCore

final class HTTPTests: XCTestCase {
    func testParsesRequestLineQueryAndBody() throws {
        var raw = Data("POST /v1/streams/audio?stream=abc&lang=ru HTTP/1.1\r\nHost: 127.0.0.1\r\nAuthorization: Bearer t\r\nContent-Length: 4\r\n\r\n".utf8)
        raw.append(Data([1, 2, 3, 4]))
        let parsed = try XCTUnwrap(HTTPServer.parse(raw))
        XCTAssertEqual(parsed.request.method, "POST")
        XCTAssertEqual(parsed.request.path, "/v1/streams/audio")
        XCTAssertEqual(parsed.request.query["stream"], "abc")
        XCTAssertEqual(parsed.request.query["lang"], "ru")
        XCTAssertEqual(parsed.request.headers["authorization"], "Bearer t")
        XCTAssertEqual(parsed.request.body, Data([1, 2, 3, 4]))
    }

    func testWaitsForCompleteBody() {
        let partial = Data("POST /x HTTP/1.1\r\nContent-Length: 10\r\n\r\nshort".utf8)
        let parsed = HTTPServer.parse(partial)
        XCTAssertEqual(parsed?.contentLength, 10)
        XCTAssertEqual(parsed?.request.body.count, 0, "body is only complete when Content-Length bytes arrived")
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
