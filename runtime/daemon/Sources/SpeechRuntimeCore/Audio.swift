import Foundation

/// Decodes the audio a client sends. Clients may send a WAV file or raw 16 kHz mono samples;
/// anything else is rejected with an explicit error instead of silently resampling, because the
/// engine's quality numbers were measured at 16 kHz.
public enum Audio {
    public static let sampleRate: Double = 16_000

    public struct Decoded {
        public var samples: [Float]
        public var sampleRate: Double
    }

    public static func decode(body: Data, rawFormat: String?) throws -> Decoded {
        if body.count >= 12, body.prefix(4) == Data("RIFF".utf8), body[8..<12] == Data("WAVE".utf8) {
            return try decodeWAV(body)
        }
        guard let rawFormat else {
            throw RuntimeError.message("body is not a WAV file; send raw samples with X-Sample-Format: s16le|f32le")
        }
        switch rawFormat {
        case "f32le":
            guard body.count % 4 == 0 else { throw RuntimeError.message("f32le body is not a multiple of 4 bytes") }
            var samples = [Float](repeating: 0, count: body.count / 4)
            _ = samples.withUnsafeMutableBytes { body.copyBytes(to: $0) }
            return Decoded(samples: samples, sampleRate: sampleRate)
        case "s16le":
            guard body.count % 2 == 0 else { throw RuntimeError.message("s16le body is not a multiple of 2 bytes") }
            var samples = [Float](repeating: 0, count: body.count / 2)
            body.withUnsafeBytes { (raw: UnsafeRawBufferPointer) in
                for i in 0..<samples.count {
                    let value = raw.loadUnaligned(fromByteOffset: i * 2, as: Int16.self)
                    samples[i] = Float(Int16(littleEndian: value)) / 32_768
                }
            }
            return Decoded(samples: samples, sampleRate: sampleRate)
        default:
            throw RuntimeError.message("unknown X-Sample-Format \(rawFormat); use s16le or f32le")
        }
    }

    public static func decodeWAV(_ data: Data) throws -> Decoded {
        guard data.count > 44 else { throw RuntimeError.message("WAV file is too short") }
        var offset = 12
        var format: UInt16 = 0
        var channels: UInt16 = 1
        var rate: UInt32 = 0
        var bits: UInt16 = 16
        var payload: Data?

        while offset + 8 <= data.count {
            let id = data[offset..<offset + 4]
            var size = Int(data.u32(offset + 4))
            offset += 8
            let end = min(offset + size, data.count)
            if id == Data("fmt ".utf8), size >= 16, offset + 16 <= data.count {
                format = data.u16(offset)
                channels = data.u16(offset + 2)
                rate = data.u32(offset + 4)
                bits = data.u16(offset + 14)
                if format == 0xFFFE, offset + 26 <= data.count {
                    format = data.u16(offset + 24)
                }
            } else if id == Data("data".utf8) {
                payload = data[offset..<end]
            }
            size += size % 2
            offset += size
        }
        guard let payload else { throw RuntimeError.message("WAV file has no data chunk") }
        guard channels >= 1 else { throw RuntimeError.message("WAV file has no channels") }

        var samples: [Float] = []
        if format == 1 && bits == 16 {
            let frameBytes = Int(channels) * 2
            let frames = payload.count / frameBytes
            samples.reserveCapacity(frames)
            payload.withUnsafeBytes { (raw: UnsafeRawBufferPointer) in
                for frame in 0..<frames {
                    var sum: Float = 0
                    for channel in 0..<Int(channels) {
                        let value = raw.loadUnaligned(fromByteOffset: frame * frameBytes + channel * 2, as: Int16.self)
                        sum += Float(Int16(littleEndian: value)) / 32_768
                    }
                    samples.append(sum / Float(channels))
                }
            }
        } else if format == 3 && bits == 32 {
            let frameBytes = Int(channels) * 4
            let frames = payload.count / frameBytes
            samples.reserveCapacity(frames)
            payload.withUnsafeBytes { (raw: UnsafeRawBufferPointer) in
                for frame in 0..<frames {
                    var sum: Float = 0
                    for channel in 0..<Int(channels) {
                        let bits32 = raw.loadUnaligned(fromByteOffset: frame * frameBytes + channel * 4, as: UInt32.self)
                        sum += Float(bitPattern: UInt32(littleEndian: bits32))
                    }
                    samples.append(sum / Float(channels))
                }
            }
        } else {
            throw RuntimeError.message("unsupported WAV encoding: format \(format), \(bits) bits; convert to 16-bit PCM or float32")
        }
        return Decoded(samples: samples, sampleRate: Double(rate))
    }
}

extension Data {
    func u16(_ offset: Int) -> UInt16 {
        let start = index(startIndex, offsetBy: offset)
        return withUnsafeBytes { $0.loadUnaligned(fromByteOffset: start, as: UInt16.self) }.littleEndian
    }

    func u32(_ offset: Int) -> UInt32 {
        let start = index(startIndex, offsetBy: offset)
        return withUnsafeBytes { $0.loadUnaligned(fromByteOffset: start, as: UInt32.self) }.littleEndian
    }

}
