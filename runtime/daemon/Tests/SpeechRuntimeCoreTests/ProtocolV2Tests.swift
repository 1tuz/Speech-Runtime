import Foundation
import XCTest
@testable import SpeechRuntimeCore

final class ProtocolV2Tests: XCTestCase {
    // MARK: - SessionEventBuffer

    private func event(_ name: String) -> WorkerClient.SessionEvent {
        WorkerClient.SessionEvent(name: name, data: [:])
    }

    func testBufferCapsAtTwentyThousandEvents() {
        var buffer = SessionEventBuffer()
        for index in 0..<(SessionEventBuffer.cap + 500) {
            buffer.append(event(index.isMultiple(of: 2) ? "utterance" : "final"))
        }
        XCTAssertEqual(buffer.events.count, SessionEventBuffer.cap, "buffer must stay at the cap")
        XCTAssertEqual(buffer.totalCount, SessionEventBuffer.cap + 500, "total count still advances cursors")
        XCTAssertEqual(buffer.droppedCount, 500)
        // A cursor at the newest event returns nothing new but still advances.
        let (events, next) = buffer.slice(since: buffer.totalCount)
        XCTAssertTrue(events.isEmpty)
        XCTAssertEqual(next, buffer.totalCount)
    }

    func testBufferSliceAfterDroppedEventsReturnsRetainedTail() {
        var buffer = SessionEventBuffer()
        for _ in 0..<(SessionEventBuffer.cap + 10) { buffer.append(event("final")) }
        // Oldest 10 fell off; a stale cursor still gets everything retained.
        let (events, next) = buffer.slice(since: 5)
        XCTAssertEqual(events.count, SessionEventBuffer.cap)
        XCTAssertEqual(next, SessionEventBuffer.cap + 10)
    }

    func testEndedSessionExpiresAfterTTL() {
        var buffer = SessionEventBuffer()
        let endedAt = Date()
        buffer.append(event("final"))
        buffer.markEnded(at: endedAt)
        XCTAssertFalse(buffer.isExpired(now: endedAt.addingTimeInterval(299)))
        XCTAssertTrue(buffer.isExpired(now: endedAt.addingTimeInterval(300)))
        // Never-ended sessions are never reaped by the TTL path.
        var live = SessionEventBuffer()
        live.append(event("final"))
        XCTAssertFalse(live.isExpired(now: endedAt.addingTimeInterval(3_600)))
    }

    func testEndedEventMarksBufferEnded() {
        var buffer = SessionEventBuffer()
        buffer.append(event("final"))
        XCTAssertFalse(buffer.ended)
        buffer.append(event("ended"))
        XCTAssertTrue(buffer.ended)
        XCTAssertEqual(buffer.endedAt != nil, true)
    }

    // MARK: - OpNames

    func testOpNamesTakeRemovesEntry() {
        let names = OpNames()
        names.insert(7, "session_end")
        XCTAssertEqual(names.take(7), "session_end")
        XCTAssertNil(names.take(7), "a second take must return nil")
    }

    // MARK: - StreamLatency arithmetic

    func testLatencyStagesSumMatchesE2E() {
        // e2e 1000 ms, silence 600 ms of audio fed at 3x -> 200 ms wall, queue 100, asr 300.
        let stages = StreamLatency.stages(
            fed: Date(timeIntervalSinceNow: -1),
            receipt: Date(),
            silenceAudioMs: 600,
            queueMs: 100,
            asrMs: 300,
            speed: 3
        )
        XCTAssertEqual(stages.e2eMs, 1000)
        XCTAssertEqual(stages.silenceMs, 200)
        XCTAssertEqual(stages.queueMs, 100)
        XCTAssertEqual(stages.asrMs, 300)
        XCTAssertEqual(stages.deliveryMs, 400)
        XCTAssertEqual(stages.rawDeliveryMs, 400)
    }

    func testLatencyDeliveryClampedToZeroButRawKept() {
        // Stages over-explain e2e (500 - 300 - 200 - 100 = -100): clamp display, keep raw.
        let stages = StreamLatency.stages(
            fed: Date(timeIntervalSinceNow: -0.5),
            receipt: Date(),
            silenceAudioMs: 300,
            queueMs: 200,
            asrMs: 100,
            speed: 1
        )
        XCTAssertEqual(stages.e2eMs, 500)
        XCTAssertEqual(stages.deliveryMs, 0, "negative delivery must be clamped for display")
        XCTAssertEqual(stages.rawDeliveryMs, -100)
    }

    func testLatencyZeroSpeedFallsBackToRealtime() {
        let stages = StreamLatency.stages(
            fed: Date(timeIntervalSinceNow: -0.4),
            receipt: Date(),
            silenceAudioMs: 400,
            queueMs: 0,
            asrMs: 0,
            speed: 0
        )
        XCTAssertEqual(stages.silenceMs, 400)
        XCTAssertEqual(stages.deliveryMs, 0)
    }

    // MARK: - IdlePolicy

    func testPruneStreamsDropsDeadGenerationOnly() async {
        let idle = IdlePolicy()
        await idle.streamOpened("dead", model: "dead-model", generation: 1)
        await idle.streamOpened("live", model: "live-model", generation: 2)
        await idle.pruneStreams(before: 2)
        let candidates = await idle.modelsToUnload(idle: 0) { _ in true }
        XCTAssertTrue(candidates.contains("dead-model"), "dead-generation stream must not pin the model")
        XCTAssertFalse(candidates.contains("live-model"), "live stream must keep its model busy")
    }
}
