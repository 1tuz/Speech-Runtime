import Foundation
import XCTest
@testable import RuntimeUIModel

final class RuntimeUIModelTests: XCTestCase {
    func testCPUFirstSampleAndRestartAreUnknown() {
        var rate = CPURate()
        XCTAssertNil(rate.sample(identity: "1/a", time: 0, totalNanos: 0))
        XCTAssertEqual(rate.sample(identity: "1/a", time: 2, totalNanos: 1_000_000_000), 50)
        XCTAssertNil(rate.sample(identity: "2/a", time: 3, totalNanos: 100))
        XCTAssertNil(rate.sample(identity: "2/b", time: 4, totalNanos: 100))
    }
    func testCPUMayExceedOneCore() {
        var rate = CPURate()
        _ = rate.sample(identity: "1", time: 1, totalNanos: 0)
        XCTAssertEqual(rate.sample(identity: "1", time: 2, totalNanos: 2_000_000_000), 200)
        XCTAssertNil(rate.sample(identity: "1", time: 2, totalNanos: 3_000_000_000))
    }
    func testIncompleteHealthDoesNotInventReadyState() throws {
        let snapshot = try JSONDecoder().decode(RuntimeSnapshot.self, from: Data("{\"ok\":true}".utf8))
        XCTAssertFalse(snapshot.hasWorker)
        XCTAssertFalse(snapshot.modelLoaded)
        XCTAssertNil(snapshot.memory)
    }
    func testHealthContract() throws {
        let data = Data("""
        {"ok":true,"streams":2,"memory":{"pid":7,"resident_bytes":3000},
         "worker":{"pid":8,"models":[{"model":"parakeet-ultra","loaded":true,"inflight":1}],
                   "memory":{"neural_bytes":700000000}}}
        """.utf8)
        let snapshot = try JSONDecoder().decode(RuntimeSnapshot.self, from: data)
        XCTAssertTrue(snapshot.hasWorker)
        XCTAssertTrue(snapshot.modelLoaded)
        XCTAssertTrue(snapshot.processing)
        XCTAssertEqual(snapshot.worker?.memory?.neural, 700_000_000)
        XCTAssertEqual(snapshot.streams, 2)
    }
    func testAgentRunsOnlyBundledExecutablesAndKeepsPathsWithSpaces() throws {
        let plist = RuntimeFiles.agentPlist(binary: "/Applications/Speech Runtime.app/Contents/MacOS/speech-runtime",
            worker: "/Applications/Speech Runtime.app/Contents/MacOS/sr-worker", config: "/tmp/config", token: "/tmp/token", label: "test")
        let data = try PropertyListSerialization.data(fromPropertyList: plist, format: .xml, options: 0)
        let roundtrip = try XCTUnwrap(PropertyListSerialization.propertyList(from: data, options: [], format: nil) as? [String: Any])
        let args = try XCTUnwrap(roundtrip["ProgramArguments"] as? [String])
        XCTAssertEqual(args.count, 9)
        XCTAssertTrue(args[0].contains("Speech Runtime.app"))
        XCTAssertNil(roundtrip["UserName"])
        XCTAssertEqual((roundtrip["KeepAlive"] as? [String: Bool])?["SuccessfulExit"], false)
    }
    func testDefaultContainsOnlyParakeetAndNoLanguageHint() {
        let config = RuntimeFiles.modelConfig(path: "/test/model.gguf")
        let models = config["models"] as? [String: [String: Any]]
        XCTAssertEqual(models?.count, 1)
        XCTAssertEqual(models?[ModelSpec.id]?["backend"] as? String, "ane")
        XCTAssertNil(models?[ModelSpec.id]?["language"])
    }
    func testModelRevisionIsPinned() {
        XCTAssertEqual(ModelSpec.revision.count, 40)
        XCTAssertTrue(ModelSpec.fileURL.absoluteString.contains(ModelSpec.revision))
        XCTAssertFalse(ModelSpec.fileURL.absoluteString.contains("/main/"))
    }
}

final class SetupWizardTests: XCTestCase {
    func testStepOrderWalksAllFiveSteps() {
        var state = SetupWizardState()
        XCTAssertEqual(state.step, .welcome)
        XCTAssertEqual(state.stepNumber, 1)
        XCTAssertEqual(state.totalSteps, 5)
        XCTAssertTrue(state.isFirst)
        for expected in [SetupWizardState.Step.login, .idle, .capabilities, .ready] {
            state.next()
            XCTAssertEqual(state.step, expected)
        }
        XCTAssertTrue(state.isLast)
        state.next()  // last step: next is a no-op
        XCTAssertEqual(state.step, .ready)
    }
    func testBackWalksReverseAndStopsAtFirst() {
        var state = SetupWizardState(step: .ready)
        state.back()
        XCTAssertEqual(state.step, .capabilities)
        state.back(); state.back(); state.back()
        XCTAssertEqual(state.step, .welcome)
        state.back()  // first step: back is a no-op
        XCTAssertEqual(state.step, .welcome)
        XCTAssertFalse(state.isLast)
    }
    func testWizardDefaultsMatchExistingPreferences() {
        let state = SetupWizardState()
        XCTAssertEqual(state.idleMinutes, 15)
        XCTAssertEqual(state.language, .system)
    }
    func testCapabilitiesAreFixedPrototypeFacts() {
        XCTAssertTrue(RuntimeCapabilities.streaming)
        XCTAssertFalse(RuntimeCapabilities.interim)
        XCTAssertTrue(RuntimeCapabilities.timestamps)
        XCTAssertFalse(RuntimeCapabilities.diarization)
    }
}

final class AppLanguageTests: XCTestCase {
    override func tearDown() {
        UserDefaults.standard.removeObject(forKey: "appLanguage")
        super.tearDown()
    }
    func testMissingKeyMeansSystem() {
        UserDefaults.standard.removeObject(forKey: "appLanguage")
        XCTAssertEqual(AppLanguage.storedValue(), .system)
        XCTAssertEqual(AppLanguage(rawValue: "system"), .system)
    }
    func testExplicitValuesRoundTrip() {
        UserDefaults.standard.set("ru", forKey: "appLanguage")
        XCTAssertEqual(AppLanguage.storedValue(), .ru)
        UserDefaults.standard.set("en", forKey: "appLanguage")
        XCTAssertEqual(AppLanguage.storedValue(), .en)
        // Unknown stored value falls back to system, never crashes.
        UserDefaults.standard.set("fr", forKey: "appLanguage")
        XCTAssertEqual(AppLanguage.storedValue(), .system)
    }
    func testLprojSelectionForExplicitChoices() {
        XCTAssertEqual(AppLanguage.en.lprojName(preferredLanguages: ["ru"]), "en")
        XCTAssertEqual(AppLanguage.ru.lprojName(preferredLanguages: ["en"]), "ru")
    }
    func testSystemFollowsPreferredLanguages() {
        XCTAssertEqual(AppLanguage.system.lprojName(preferredLanguages: ["ru-RU", "en-US"]), "ru")
        XCTAssertEqual(AppLanguage.system.lprojName(preferredLanguages: ["en-US", "ru"]), "en")
        XCTAssertEqual(AppLanguage.system.lprojName(preferredLanguages: ["de-DE"]), "en")
        XCTAssertEqual(AppLanguage.system.lprojName(preferredLanguages: []), "en")
    }
    func testStorePersistsRawValue() {
        AppLanguage.ru.store()
        XCTAssertEqual(UserDefaults.standard.string(forKey: "appLanguage"), "ru")
    }
}

// MARK: - Language bundle resolution (regression: switching language did nothing)

final class LanguageBundleResolutionTests: XCTestCase {
    func testLprojDirectoryIsLoadableAndDiffersFromContainer() throws {
        // The bug: Bundle(path:) was given the .strings FILE path (not a bundle),
        // returned nil, and L() silently fell back to the container bundle, which
        // follows the process-wide AppleLanguages lookup — the in-app switch had
        // no effect. The correct target is the .lproj directory.
        let container = Bundle(for: LanguageBundleResolutionTests.self)
        // The strings live in the app target's resource bundle; locate it
        // next to the test bundle (same build products directory).
        let testBundle = Bundle(for: LanguageBundleResolutionTests.self)
        let productsURL = testBundle.bundleURL.deletingLastPathComponent()
        let appResourceURL = productsURL
            .appendingPathComponent("SpeechRuntimeMenu_SpeechRuntimeApp.bundle")
        let moduleBundle = Bundle(url: appResourceURL) ?? testBundle
        let enPath = moduleBundle.path(forResource: "Localizable", ofType: "strings",
                                       inDirectory: nil, forLocalization: "en")
        let ruPath = moduleBundle.path(forResource: "Localizable", ofType: "strings",
                                       inDirectory: nil, forLocalization: "ru")
        // In test context the module may be flattened; guard rather than skip so
        // a packaging change that drops .lproj dirs fails loudly.
        guard let enStrings = enPath, let ruStrings = ruPath else {
            XCTFail("module bundle has no en/ru .lproj Localizable.strings; localization packaging is broken")
            return
        }
        let enLproj = (enStrings as NSString).deletingLastPathComponent
        let ruLproj = (ruStrings as NSString).deletingLastPathComponent
        let en = try XCTUnwrap(Bundle(path: enLproj), "en.lproj directory must be loadable as a bundle")
        let ru = try XCTUnwrap(Bundle(path: ruLproj), "ru.lproj directory must be loadable as a bundle")
        XCTAssertNotEqual(
            en.localizedString(forKey: "power.on", value: "?", table: nil),
            ru.localizedString(forKey: "power.on", value: "?", table: nil),
            "en and ru bundles must return different strings; otherwise switching is invisible"
        )
        _ = container
    }
}
