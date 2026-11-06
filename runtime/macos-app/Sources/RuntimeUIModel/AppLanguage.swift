import Foundation

/// UI language preference stored in UserDefaults ("appLanguage").
/// "system" follows the user's preferred localization; missing key also means "system",
/// in which case the wizard offers English as the suggested default.
public enum AppLanguage: String, CaseIterable, Sendable {
    case system
    case en
    case ru

    /// Default stored value when the preference key is absent.
    public static let defaultStoredValue = "system"

    public static func storedValue(in defaults: UserDefaults = .standard) -> AppLanguage {
        AppLanguage(rawValue: defaults.string(forKey: "appLanguage") ?? defaultStoredValue) ?? .system
    }

    public func store(in defaults: UserDefaults = .standard) {
        defaults.set(rawValue, forKey: "appLanguage")
    }

    /// Resolves the .lproj directory name to load. The bundle only ships en and ru;
    /// any other preferred language falls back to English.
    public func lprojName(preferredLanguages: [String]) -> String {
        switch self {
        case .en: return "en"
        case .ru: return "ru"
        case .system:
            for language in preferredLanguages {
                if language.hasPrefix("ru") { return "ru" }
                if language.hasPrefix("en") { return "en" }
            }
            return "en"
        }
    }
}
