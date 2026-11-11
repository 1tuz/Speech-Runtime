import Foundation
import XCTest
@testable import SpeechRuntimeCore

/// Router-level and TtsHost-level tests driven by a fake TTS sidecar: a small
/// Python script that speaks the same NDJSON protocol as
/// `soniqo-tts-sidecar` (immediate `ping`/`init_model`/`model_info`/
/// `unload_model`, delayed synthesis) and prints the same
/// `[sidecar] mem …` stderr lines. No model weights are needed.
final class TtsHostTests: XCTestCase {
    private var workDir: URL!
    private var routers: [Router] = []

    override func setUpWithError() throws {
        workDir = FileManager.default.temporaryDirectory
            .appendingPathComponent("tts-tests-\(UUID().uuidString)", isDirectory: true)
        try FileManager.default.createDirectory(at: workDir, withIntermediateDirectories: true)
    }

    override func tearDown() async throws {
        // Kill the fake sidecars: a live child keeps xctest from exiting.
        for router in routers { await router.tts.stop() }
        try? FileManager.default.removeItem(at: workDir)
    }

    // MARK: - Fake sidecar

    /// Writes and chmods the fake sidecar. `delaySeconds` is applied to every
    /// synth command; replies are written as single NDJSON lines.
    private func fakeSidecar(delaySeconds: Double) throws -> String {
        let url = workDir.appendingPathComponent("fake-tts-sidecar.py")
        let script = """
        #!/usr/bin/env python3
        import json, os, sys, time

        delay = float(os.environ.get("FAKE_DELAY", "\(delaySeconds)"))
        for raw in sys.stdin:
            raw = raw.strip()
            if not raw:
                continue
            try:
                req = json.loads(raw)
            except ValueError:
                sys.stderr.write("[sidecar] malformed request\\n")
                continue
            command = req.get("command", "")
            rid = req.get("id", "")
            if command == "ping":
                reply = {"id": rid, "ok": True, "result": {"pong": True, "version": "0.0.0-fake"}}
            elif command == "init_model":
                sys.stderr.write("[sidecar] mem post-load-fake active=10M cache=2M peak=40M rss=180M footprint=210M\\n")
                sys.stderr.flush()
                reply = {"id": rid, "ok": True, "result": {"ready": True}}
            elif command == "model_info":
                model_id = req.get("modelId", "")
                path = os.path.expanduser("~/Library/Caches/fake-tts/" + model_id)
                exists = False
                bytes_ = 0
                if os.path.isdir(path):
                    exists = True
                    for root, _, files in os.walk(path):
                        for name in files:
                            try:
                                bytes_ += os.path.getsize(os.path.join(root, name))
                            except OSError:
                                pass
                reply = {"id": rid, "ok": True,
                         "result": {"exists": exists, "bytes": bytes_, "modelId": model_id}}
            elif command == "unload_model":
                reply = {"id": rid, "ok": True, "result": {"unloaded": True}}
            elif command.startswith("synthesize"):
                time.sleep(delay)
                out_dir = os.environ.get("FAKE_OUT_DIR", "/tmp")
                out_path = os.path.join(out_dir, "fake-synth-%s.wav" % rid)
                # 16-bit mono 16000 Hz, 0.5 s of silence + a RIFF header.
                import struct, wave
                with wave.open(out_path, "wb") as w:
                    w.setnchannels(1)
                    w.setsampwidth(2)
                    w.setframerate(16000)
                    w.writeframes(b"\\x00\\x00" * 8000)
                reply = {"id": rid, "ok": True,
                         "result": {"audioPath": out_path, "sampleRate": 16000,
                                    "durationSec": 0.5}}
            else:
                reply = {"id": rid, "ok": False, "error": "unknown command %s" % command}
            sys.stdout.write(json.dumps(reply) + "\\n")
            sys.stdout.flush()
        sys.stderr.write("[sidecar] eof\\n")
        """
        try script.write(to: url, atomically: true, encoding: .utf8)
        try FileManager.default.setAttributes(
            [.posixPermissions: 0o755], ofItemAtPath: url.path)
        return url.path
    }

    private func makeRouter(binary: String) -> Router {
        let spec: [String: Any] = [
            "models": [:],
            "tts": [
                "binary": binary,
                "models": [
                    "espeech": [
                        "engine": "espeech",
                        "modelId": "local/ESpeech-TTS-1-RL-V2-MLX-fp16",
                        "variants": ["v1", "v2"],
                    ],
                ],
            ],
        ]
        let snapshot = RuntimeConfig(jsonObject: spec)
        let router = Router(
            token: AuthToken(value: "test-token"),
            worker: WorkerClient(workerPath: "/nonexistent/sr-worker") { _ in },
            config: { snapshot },
            idle: IdlePolicy(),
            tts: TtsHost(binaryPath: binary, log: { _ in }),
            voices: VoiceStore(),
            log: { _ in }
        )
        routers.append(router)
        return router
    }

    private func request(
        _ method: String, _ path: String, body: Any? = nil
    ) -> HTTPRequest {
        let data = body.flatMap { try? JSONSerialization.data(withJSONObject: $0) } ?? Data()
        return HTTPRequest(
            method: method, path: path, query: [:],
            headers: ["authorization": "Bearer test-token"], body: data)
    }

    private func json(_ response: HTTPResponse) throws -> [String: Any] {
        try XCTUnwrap(try JSONSerialization.jsonObject(with: response.body) as? [String: Any])
    }

    private func waitFor(
        _ condition: () async -> Bool, timeout: TimeInterval = 5
    ) async -> Bool {
        let deadline = Date().addingTimeInterval(timeout)
        while Date() < deadline {
            if await condition() { return true }
            try? await Task.sleep(nanoseconds: 50_000_000)
        }
        return await condition()
    }

    // MARK: - Item 1: temp reference WAV is deleted after the request

    func testSynthesizeDeletesTempReferenceWav() async throws {
        // The caller's reference lives outside the temp dir: its survival is
        // part of the contract (caller-supplied files must never be deleted).
        let reference = workDir.appendingPathComponent("caller-reference.wav")
        try Data("RIFFxxxxWAVEfmt ".utf8).write(to: reference)
        let router = makeRouter(binary: try fakeSidecar(delaySeconds: 0.1))
        let before = (try? FileManager.default.contentsOfDirectory(
            atPath: FileManager.default.temporaryDirectory.path))?
            .filter { $0.hasPrefix("runtime-tts-") } ?? []
        let body: [String: Any] = [
            "model": "espeech",
            "text": "привет",
            "referenceAudioBase64": Data("RIFFxxxxWAVEfmt ".utf8).base64EncodedString(),
            "referenceText": "привет",
        ]
        let response = await router.handle(request("POST", "/v1/tts/synthesize", body: body))
        XCTAssertEqual(response.status, 200, "synthesis must succeed first")
        // The temp file must be gone: the call wrote one and the defer removed it.
        let after = try FileManager.default.contentsOfDirectory(
            atPath: FileManager.default.temporaryDirectory.path)
            .filter { $0.hasPrefix("runtime-tts-") }
        XCTAssertEqual(
            Set(after).subtracting(before), [],
            "no new runtime-tts-* temp file may survive the request")
        XCTAssertTrue(
            FileManager.default.fileExists(atPath: reference.path),
            "caller-supplied reference audio must never be deleted")
    }

    // MARK: - Item 2: variant tracking

    func testLoadVariantTrackingAndReuse() async throws {
        let router = makeRouter(binary: try fakeSidecar(delaySeconds: 0))
        let tts = router.tts
        let entry = try XCTUnwrap(router.config().ttsModels["espeech"])

        // Load v1 explicitly.
        _ = try await tts.load(model: "espeech", entry: entry, variant: "v1")
        let stats1 = try await tts.stats()
        XCTAssertEqual(stats1["loadedVariant"] as? String, "v1")

        // nil variant reuses the resident model (same engine+modelId).
        let reuse = try await tts.load(model: "espeech", entry: entry, variant: nil)
        XCTAssertEqual(reuse["reused"] as? Bool, true)

        // The same explicit variant also reuses.
        let sameVariant = try await tts.load(model: "espeech", entry: entry, variant: "v1")
        XCTAssertEqual(sameVariant["reused"] as? Bool, true)

        // A different explicit variant forces a reload.
        let swap = try await tts.load(model: "espeech", entry: entry, variant: "v2")
        XCTAssertEqual(swap["reused"] as? Bool == true, false, "different variant must re-init")
        let stats2 = try await tts.stats()
        XCTAssertEqual(stats2["loadedVariant"] as? String, "v2")
        // nil still means "whatever is resident" after the swap.
        _ = try await tts.load(model: "espeech", entry: entry, variant: nil)
        let stats3 = try await tts.stats()
        XCTAssertEqual(stats3["loadedVariant"] as? String, "v2")
    }

    func testModelsListingCarriesLoadedVariant() async throws {
        let router = makeRouter(binary: try fakeSidecar(delaySeconds: 0))
        _ = await router.handle(
            request("POST", "/v1/tts/models/load", body: ["model": "espeech", "variant": "v2"]))
        let response = await router.handle(request("GET", "/v1/tts/models"))
        let payload = try json(response)
        let models = try XCTUnwrap(payload["models"] as? [[String: Any]])
        let row = try XCTUnwrap(models.first { $0["model"] as? String == "espeech" })
        XCTAssertEqual(row["loaded"] as? Bool, true)
        XCTAssertEqual(row["loadedVariant"] as? String, "v2")
        XCTAssertEqual(payload["loadedVariant"] as? String, "v2")
    }

    // MARK: - Item 3: idle unload never fires while a request is in flight

    func testUnloadIdleTTSRefusesWhileInFlight() async throws {
        // Slow fake: the synth reply takes 1.5 s, so a request is in flight
        // when the idle monitor asks for an unload.
        let router = makeRouter(binary: try fakeSidecar(delaySeconds: 1.5))
        _ = try await router.handle(
            request("POST", "/v1/tts/models/load", body: ["model": "espeech"]))
        let body: [String: Any] = [
            "model": "espeech",
            "text": "hi",
            "referenceAudioBase64": Data("RIFF".utf8).base64EncodedString(),
            "referenceText": "hi",
        ]
        let synthTask = Task { await router.handle(request("POST", "/v1/tts/synthesize", body: body)) }
        // Give the request time to be pending on the slow sidecar.
        try await Task.sleep(nanoseconds: 300_000_000)
        let tts = router.tts
        await tts.unloadIdleTTS()
        let stats = try await tts.stats()
        XCTAssertEqual(
            stats["loaded"] as? Bool, true,
            "idle unload must not drop the model while a request is in flight")
        _ = await synthTask.value
    }

    func testLastTtsActivityAdvancesOnSynthesize() async throws {
        let router = makeRouter(binary: try fakeSidecar(delaySeconds: 0.05))
        let body: [String: Any] = [
            "model": "espeech",
            "text": "hi",
            "referenceAudioBase64": Data("RIFF".utf8).base64EncodedString(),
            "referenceText": "hi",
        ]
        _ = await router.handle(request("POST", "/v1/tts/synthesize", body: body))
        let idle = await router.tts.ttsIdleSeconds
        XCTAssertLessThan(idle, 5, "synthesizing must refresh the TTS activity timestamp")
    }

    // MARK: - Item 4: memory parsed from the sidecar's stderr

    func testHealthAndModelsExposeParsedMemory() async throws {
        let router = makeRouter(binary: try fakeSidecar(delaySeconds: 0))
        _ = await router.handle(
            request("POST", "/v1/tts/models/load", body: ["model": "espeech"]))
        // The fake sidecar prints its mem line during init_model; wait for the
        // parser to pick it up.
        let tts = router.tts
        let seen = await waitFor { await tts.memoryReport != nil }
        XCTAssertTrue(seen, "the [sidecar] mem line must be parsed into memoryReport")
        let memory = await tts.memoryReport
        XCTAssertEqual(memory?["rssMb"] as? Int, 180)
        XCTAssertEqual(memory?["footprintMb"] as? Int, 210)
        XCTAssertEqual(memory?["peakMb"] as? Int, 40)

        let health = try json(await router.handle(request("GET", "/health")))
        let ttsBlock = try XCTUnwrap(health["tts"] as? [String: Any])
        XCTAssertEqual((ttsBlock["memory"] as? [String: Any])?["rssMb"] as? Int, 180)

        let models = try json(await router.handle(request("GET", "/v1/tts/models")))
        let row = try XCTUnwrap(
            (models["models"] as? [[String: Any]])?
                .first { $0["model"] as? String == "espeech" })
        XCTAssertEqual((row["memory"] as? [String: Any])?["footprintMb"] as? Int, 210)
    }

    // MARK: - Item 5: queue-cancel

    func testCancelPendingRequestFailsClientWithCancelledMessage() async throws {
        // The synth request is pending on the slow fake sidecar; cancelling
        // its requestId must fail it with the cancelled message.
        let router = makeRouter(binary: try fakeSidecar(delaySeconds: 1.0))
        let body: [String: Any] = [
            "model": "espeech",
            "text": "hi",
            "requestId": "cancel-me",
            "referenceAudioBase64": Data("RIFF".utf8).base64EncodedString(),
            "referenceText": "hi",
        ]
        let synthTask = Task { await router.handle(request("POST", "/v1/tts/synthesize", body: body)) }
        // Wait until the request is pending (in flight, reply not yet sent).
        try await Task.sleep(nanoseconds: 300_000_000)
        let cancelResponse = await router.handle(
            request("POST", "/v1/tts/cancel", body: ["requestId": "cancel-me"]))
        let cancel = try json(cancelResponse)
        XCTAssertEqual(cancelResponse.status, 200)
        XCTAssertEqual(cancel["cancelled"] as? Bool, true)
        XCTAssertEqual((cancel["pending"] as? NSNumber)?.intValue, 1)

        let synthResponse = await synthTask.value
        let error = try XCTUnwrap(
            (try json(synthResponse))["error"] as? String,
            "the cancelled client must get an error response, not audio")
        XCTAssertTrue(
            error.lowercased().contains("cancelled"),
            "the error must carry the cancelled message: \(error)")
    }

    func testCancelUnknownRequestIdIsNoOp() async throws {
        let router = makeRouter(binary: try fakeSidecar(delaySeconds: 0))
        let response = await router.handle(
            request("POST", "/v1/tts/cancel", body: ["requestId": "never-seen"]))
        let payload = try json(response)
        XCTAssertEqual(response.status, 200)
        XCTAssertEqual(payload["cancelled"] as? Bool, false)
        XCTAssertEqual((payload["pending"] as? NSNumber)?.intValue, 0)
    }

    // MARK: - Catalog + happy path + unknown model

    func testModelsCatalogShapeWithArtifact() async throws {
        // A real dir on disk so the fake's model_info reports exists/bytes.
        let modelId = "tests-artifact"
        let cacheRoot = NSHomeDirectory() + "/Library/Caches/fake-tts"
        let target = (cacheRoot as NSString).appendingPathComponent(modelId)
        try FileManager.default.createDirectory(
            atPath: cacheRoot, withIntermediateDirectories: true)
        if FileManager.default.fileExists(atPath: target) {
            try FileManager.default.removeItem(atPath: target)
        }
        try FileManager.default.createDirectory(
            atPath: target, withIntermediateDirectories: true)
        try Data(repeating: 7, count: 1234).write(
            to: URL(fileURLWithPath: target).appendingPathComponent("weights.bin"))
        defer { try? FileManager.default.removeItem(atPath: target) }

        let binary = try fakeSidecar(delaySeconds: 0)
        let spec: [String: Any] = [
            "models": [:],
            "tts": [
                "binary": binary,
                "models": [
                    "art": ["engine": "espeech", "modelId": modelId],
                ],
            ],
        ]
        let snapshot = RuntimeConfig(jsonObject: spec)
        let router = Router(
            token: AuthToken(value: "test-token"),
            worker: WorkerClient(workerPath: "/nonexistent/sr-worker") { _ in },
            config: { snapshot },
            idle: IdlePolicy(),
            tts: TtsHost(binaryPath: binary, log: { _ in }),
            voices: VoiceStore(),
            log: { _ in }
        )
        routers.append(router)
        let response = await router.handle(request("GET", "/v1/tts/models"))
        XCTAssertEqual(response.status, 200)
        let payload = try json(response)
        let models = try XCTUnwrap(payload["models"] as? [[String: Any]])
        let row = try XCTUnwrap(models.first { $0["model"] as? String == "art" })
        XCTAssertEqual(row["engine"] as? String, "espeech")
        XCTAssertEqual(row["modelId"] as? String, modelId)
        XCTAssertEqual(row["loaded"] as? Bool, false)
        XCTAssertEqual(row["requiresReferenceAudio"] as? Bool, true)
        XCTAssertEqual(row["exists"] as? Bool, true, "artifact dir exists on disk")
        XCTAssertEqual((row["bytes"] as? NSNumber)?.intValue, 1234, "bytes computed from the dir")
    }

    func testLoadSynthesizeUnloadHappyPath() async throws {
        setenv("FAKE_OUT_DIR", workDir.path, 1)
        defer { unsetenv("FAKE_OUT_DIR") }
        let router = makeRouter(binary: try fakeSidecar(delaySeconds: 0))
        let referenceWav = Data("RIFF0000WAVEfmt ".utf8)
        let body: [String: Any] = [
            "model": "espeech",
            "text": "проверка",
            "referenceAudioBase64": referenceWav.base64EncodedString(),
            "referenceText": "проверка",
        ]
        // load
        let loadResponse = await router.handle(
            request("POST", "/v1/tts/models/load", body: ["model": "espeech"]))
        XCTAssertEqual(loadResponse.status, 200)
        // synth
        let synthResponse = await router.handle(
            request("POST", "/v1/tts/synthesize", body: body))
        XCTAssertEqual(synthResponse.status, 200)
        let payload = try json(synthResponse)
        let audioBase64 = try XCTUnwrap(payload["audioBase64"] as? String)
        let wav = try XCTUnwrap(Data(base64Encoded: audioBase64))
        // RIFF length must match the decoded audio byte count.
        XCTAssertGreaterThanOrEqual(wav.count, 44)
        XCTAssertEqual(String(data: wav[0..<4], encoding: .ascii), "RIFF")
        var length = 0
        for (index, byte) in zip(0..., wav[4..<8]) {
            length |= Int(byte) << (8 * index)
        }
        XCTAssertEqual(length, wav.count - 8, "RIFF chunk length matches the WAV bytes")
        XCTAssertEqual((payload["sampleRate"] as? NSNumber)?.intValue, 16000)
        // unload
        let unloadResponse = await router.handle(
            request("POST", "/v1/tts/models/unload", body: ["model": "espeech"]))
        XCTAssertEqual(unloadResponse.status, 200)
    }

    func testUnknownModelIsRejectedWith400() async throws {
        let router = makeRouter(binary: try fakeSidecar(delaySeconds: 0))
        let response = await router.handle(
            request("POST", "/v1/tts/models/load", body: ["model": "nope"]))
        XCTAssertEqual(response.status, 400)
        let error = try XCTUnwrap((try json(response))["error"] as? String)
        XCTAssertTrue(error.contains("unknown TTS model"), "got \(error)")
    }
}
