import Foundation

/// Bearer token for the loopback API. The token is a file with mode 0600; a client that can read
/// it is the same user, which is the trust boundary of a local service.
public struct AuthToken: Sendable {
    public let value: String

    public static func loadOrCreate(at url: URL) throws -> AuthToken {
        let fm = FileManager.default
        try fm.createDirectory(at: url.deletingLastPathComponent(), withIntermediateDirectories: true)
        if let data = try? Data(contentsOf: url),
           let text = String(data: data, encoding: .utf8)?.trimmingCharacters(in: .whitespacesAndNewlines),
           !text.isEmpty {
            return AuthToken(value: text)
        }
        var bytes = [UInt8](repeating: 0, count: 32)
        let status = SecRandomCopyBytes(kSecRandomDefault, bytes.count, &bytes)
        guard status == errSecSuccess else {
            throw RuntimeError.message("SecRandomCopyBytes failed: \(status)")
        }
        let token = Data(bytes).base64EncodedString()
            .replacingOccurrences(of: "+", with: "-")
            .replacingOccurrences(of: "/", with: "_")
            .replacingOccurrences(of: "=", with: "")
        try Data(token.utf8).write(to: url, options: [.atomic])
        try fm.setAttributes([.posixPermissions: 0o600], ofItemAtPath: url.path)
        return AuthToken(value: token)
    }

    public func matches(_ header: String?) -> Bool {
        guard let header, header.hasPrefix("Bearer ") else { return false }
        let presented = String(header.dropFirst("Bearer ".count))
        let a = Array(value.utf8), b = Array(presented.utf8)
        guard a.count == b.count else { return false }
        var diff: UInt8 = 0
        for i in 0..<a.count { diff |= a[i] ^ b[i] }
        return diff == 0
    }
}

public enum RuntimeError: Error, CustomStringConvertible {
    case message(String)

    public var description: String {
        switch self {
        case .message(let text): return text
        }
    }
}
