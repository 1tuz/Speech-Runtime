import Foundation
import XCTest
@testable import SpeechRuntimeCore

/// Thread-safe box for state shared between a test and a parked continuation.
private final class Box<T>: @unchecked Sendable {
    private let lock = NSLock()
    private var value: T

    init(_ value: T) {
        self.value = value
    }

    var current: T {
        lock.lock(); defer { lock.unlock() }
        return value
    }

    func mutate(_ body: (inout T) -> Void) {
        lock.lock(); defer { lock.unlock() }
        body(&value)
    }
}

private enum TestFailure: Error {
    case timedOut
}

final class TimeoutTests: XCTestCase {
    func testFiresOnce() async {
        let fired = Box(0)
        let timer = CancelableTimeout(nanoseconds: 20_000_000) { fired.mutate { $0 += 1 } }
        try? await Task.sleep(nanoseconds: 150_000_000)
        XCTAssertEqual(fired.current, 1)
        XCTAssertTrue(timer.didFire)
    }

    func testCancelBeforeFireNeverRunsTheAction() async {
        // The old implementation used `try? await Task.sleep`, so a cancelled timer *fell through*
        // into the timeout branch; this is the regression guard for that.
        let fired = Box(0)
        let timer = CancelableTimeout(nanoseconds: 20_000_000) { fired.mutate { $0 += 1 } }
        timer.cancel()
        try? await Task.sleep(nanoseconds: 150_000_000)
        XCTAssertEqual(fired.current, 0)
        XCTAssertFalse(timer.didFire)
    }

    func testCancelAfterFireDoesNotRunAgain() async {
        let fired = Box(0)
        let timer = CancelableTimeout(nanoseconds: 10_000_000) { fired.mutate { $0 += 1 } }
        try? await Task.sleep(nanoseconds: 100_000_000)
        timer.cancel()
        try? await Task.sleep(nanoseconds: 50_000_000)
        XCTAssertEqual(fired.current, 1)
    }
}

final class PendingRepliesTests: XCTestCase {
    private func parked<T>(
        _ register: @escaping (CheckedContinuation<T, Error>) -> Void
    ) -> Task<Result<T, Error>, Never> {
        Task {
            do {
                return .success(try await withCheckedThrowingContinuation { continuation in register(continuation) })
            } catch {
                return .failure(error)
            }
        }
    }

    private func waitUntil(_ condition: @Sendable () -> Bool, seconds: TimeInterval = 2) async {
        let deadline = Date().addingTimeInterval(seconds)
        while !condition() {
            if Date() > deadline { return XCTFail("condition not reached") }
            try? await Task.sleep(nanoseconds: 2_000_000)
        }
    }

    func testTakeResumesOnceAndCancelsTheTimer() async {
        let pending = PendingReplies()
        let fired = Box(0)
        let timer = CancelableTimeout(nanoseconds: 50_000_000) { fired.mutate { $0 += 1 } }
        let waiter = parked { continuation in
            XCTAssertTrue(pending.insert(1, continuation))
            pending.arm(1, timer)
        }
        await waitUntil { pending.count == 1 }

        let stolen = pending.take(1)
        XCTAssertNotNil(stolen)
        stolen?.resume(returning: ["ok": true])
        let result = await waiter.value
        XCTAssertEqual(try? result.get()["ok"] as? Bool, true)

        try? await Task.sleep(nanoseconds: 150_000_000)
        XCTAssertEqual(fired.current, 0, "the reply won the race; the timeout must not run")
        let remaining = pending.count
        XCTAssertEqual(remaining, 0)
    }

    func testTimeoutResumesTheParkedWaiter() async {
        let pending = PendingReplies()
        let waiter = parked { continuation in
            XCTAssertTrue(pending.insert(2, continuation))
            pending.arm(2, CancelableTimeout(nanoseconds: 20_000_000) { [pending] in
                guard let continuation = pending.take(2) else { return }
                continuation.resume(throwing: TestFailure.timedOut)
            })
        }
        let result = await waiter.value
        XCTAssertThrowsError(try result.get())
        let remaining = pending.count
        XCTAssertEqual(remaining, 0)
    }

    func testArmAfterTakeCancelsTheTimerInsteadOfLeakingIt() async {
        let pending = PendingReplies()
        let fired = Box(0)
        let waiter = parked { continuation in
            XCTAssertTrue(pending.insert(3, continuation))
        }
        await waitUntil { pending.count == 1 }
        let stolen = pending.take(3)
        // The reply raced the registration; the timer is armed only afterwards.
        let timer = CancelableTimeout(nanoseconds: 20_000_000) { fired.mutate { $0 += 1 } }
        pending.arm(3, timer)
        stolen?.resume(returning: [:])
        _ = await waiter.value
        try? await Task.sleep(nanoseconds: 100_000_000)
        XCTAssertEqual(fired.current, 0)
    }

    func testAbandonBeforeRegistrationRejectsTheLateWaiter() async {
        // A caller that gives up before its continuation is registered must not hang the waiter.
        let pending = PendingReplies()
        XCTAssertNil(pending.abandon(4))
        var resumed = false
        let waiter = parked { continuation in
            let accepted = pending.insert(4, continuation)
            if !accepted { continuation.resume(throwing: CancellationError()) }
            resumed = !accepted
        }
        _ = await waiter.value
        XCTAssertTrue(resumed)
        XCTAssertEqual(pending.count, 0)
    }

    func testFailAllResumesEveryWaiterAndClearsTimers() async {
        let pending = PendingReplies()
        let fired = Box(0)
        let first = parked { continuation in
            XCTAssertTrue(pending.insert(5, continuation))
            pending.arm(5, CancelableTimeout(nanoseconds: 50_000_000) { fired.mutate { $0 += 1 } })
        }
        let second = parked { continuation in
            XCTAssertTrue(pending.insert(6, continuation))
        }
        await waitUntil { pending.count == 2 }
        pending.failAll("worker exited with status 9")
        let firstResult = await first.value
        let secondResult = await second.value
        XCTAssertThrowsError(try firstResult.get())
        XCTAssertThrowsError(try secondResult.get())
        XCTAssertEqual(pending.count, 0)
        try? await Task.sleep(nanoseconds: 120_000_000)
        XCTAssertEqual(fired.current, 0)
    }
}

final class WaiterTableTests: XCTestCase {
    private func waitUntil(_ condition: @Sendable () -> Bool, seconds: TimeInterval = 2) async {
        let deadline = Date().addingTimeInterval(seconds)
        while !condition() {
            if Date() > deadline { return XCTFail("condition not reached") }
            try? await Task.sleep(nanoseconds: 2_000_000)
        }
    }

    func testWakeReleasesOnlyTheMatchingSession() async {
        let table = WaiterTable(cap: 8)
        let wokenA = expectation(description: "session a woken")
        let wokenB = expectation(description: "session b must stay parked")
        wokenB.isInverted = true

        let taskA = Task {
            await withCheckedContinuation { continuation in _ = table.add(session: "a", continuation: continuation) }
            wokenA.fulfill()
        }
        let taskB = Task {
            await withCheckedContinuation { continuation in _ = table.add(session: "b", continuation: continuation) }
            wokenB.fulfill()
        }
        await waitUntil { table.count == 2 }

        table.wake(session: "a")
        await fulfillment(of: [wokenA], timeout: 2)
        await fulfillment(of: [wokenB], timeout: 0.3)

        table.wake(session: nil)
        _ = await taskA.value
        _ = await taskB.value
        XCTAssertEqual(table.count, 0)
    }

    func testCapRejectsTheExtraWaiter() async {
        let table = WaiterTable(cap: 1)
        let first = Task {
            await withCheckedContinuation { continuation in _ = table.add(session: "a", continuation: continuation) }
        }
        await waitUntil { table.count == 1 }

        // The rejection branch must resume the continuation itself: nobody else will ever
        // wake a waiter the table refused to register.
        var accepted: UInt64?
        await withCheckedContinuation { continuation in
            accepted = table.add(session: "b", continuation: continuation)
            if accepted == nil { continuation.resume() }
        }
        XCTAssertNil(accepted, "at the cap the caller must be told to return immediately")
        XCTAssertEqual(table.count, 1)

        table.wake(session: nil)
        _ = await first.value
    }

    func testExpireReleasesExactlyOneWaiter() async {
        let table = WaiterTable(cap: 4)
        let expired = expectation(description: "expired waiter released")
        let other = expectation(description: "unrelated waiter stays parked")
        other.isInverted = true

        let taskA = Task {
            await withCheckedContinuation { continuation in _ = table.add(session: "a", continuation: continuation) }
            expired.fulfill()
        }
        let taskB = Task {
            await withCheckedContinuation { continuation in _ = table.add(session: "b", continuation: continuation) }
            other.fulfill()
        }
        await waitUntil { table.count == 2 }
        table.expire(1)
        await fulfillment(of: [expired], timeout: 2)
        await fulfillment(of: [other], timeout: 0.3)
        table.wake(session: nil)
        _ = await taskA.value
        _ = await taskB.value
    }
}

final class RetiredSessionsTests: XCTestCase {
    func testRetiredIdIsRememberedAndRevived() {
        var retired = RetiredSessions()
        retired.retire("s1")
        XCTAssertTrue(retired.contains("s1"))
        retired.revive("s1")
        XCTAssertFalse(retired.contains("s1"), "reopening the same id makes it a live session again")
    }

    func testPruneDropsIdsOlderThanTheTTL() {
        var retired = RetiredSessions()
        let now = Date()
        retired.retire("old", now: now.addingTimeInterval(-RetiredSessions.ttl - 1))
        retired.retire("fresh", now: now)
        retired.prune(now: now)
        XCTAssertFalse(retired.contains("old"))
        XCTAssertTrue(retired.contains("fresh"))
    }

    func testPruneKeepsTheTableBounded() {
        var retired = RetiredSessions()
        let now = Date()
        for index in 0...(RetiredSessions.cap + 10) {
            retired.retire("s\(index)", now: now.addingTimeInterval(Double(index)))
        }
        XCTAssertLessThanOrEqual(retired.count, RetiredSessions.cap)
        XCTAssertTrue(retired.contains("s\(RetiredSessions.cap + 10)"), "the newest ids survive")
    }
}

final class MemoryReportTests: XCTestCase {
    func testReportsCurrentProcessCountersWithConsistentPeaks() throws {
        let report = MemoryReport.capture()
        XCTAssertTrue(report.taskVMInfoOK || report.rusageOK, "at least one kernel API must answer")
        let resident = try XCTUnwrap(report.residentBytes)
        XCTAssertGreaterThan(resident, 0)

        // Peak counters are historical maxima: the current value can never exceed them.
        if let current = report.neuralBytes, let lifetime = report.neuralLifetimeMaxBytes {
            XCTAssertLessThanOrEqual(current, lifetime)
        }
        if let interval = report.neuralIntervalMaxBytes, let lifetime = report.neuralLifetimeMaxBytes {
            XCTAssertLessThanOrEqual(interval, lifetime)
        }
        if let current = report.physFootprintBytes, let lifetime = report.physFootprintLifetimeMaxBytes {
            XCTAssertLessThanOrEqual(current, lifetime)
        }
    }

    func testUnavailableAPIsAreNullNotZero() throws {
        // The contract that keeps a missing measurement from looking like "no memory used".
        let json = MemoryReport.capture().json
        XCTAssertNotNil(json["resident_bytes"] as? UInt64)
        for key in ["neural_bytes", "neural_lifetime_max_bytes", "neural_interval_max_bytes"] {
            let value = json[key]
            XCTAssertTrue(value is UInt64 || value is NSNull, "\(key) must be a number or null, got \(String(describing: value))")
        }
    }
}

/// Worker-loss guard. A worker `SIGKILL` used to abort the daemon with
/// `NSInvalidArgumentException: task still running` raised from `-[NSConcreteTask dealloc]`, because
/// the crash path read `terminationStatus` and dropped the last reference while the child was
/// still unreaped. `WorkerClient.exitStatus(of:)` must reap instead of raising — a regression here
/// aborts this whole test process, which is exactly the failure being guarded.
final class WorkerReapTests: XCTestCase {
    func testSigkilledChildYieldsItsStatusAndSurvivesRelease() throws {
        let task = Process()
        task.executableURL = URL(fileURLWithPath: "/bin/sleep")
        task.arguments = ["30"]
        try task.run()
        kill(task.processIdentifier, SIGKILL)

        let status = WorkerClient.exitStatus(of: task)

        XCTAssertEqual(status, SIGKILL, "a killed child reports the killing signal")
        XCTAssertFalse(task.isRunning)
        XCTAssertEqual(task.terminationStatus, SIGKILL, "the child must be reaped, not left running")
    }

    func testLiveChildIsTerminatedAndReaped() throws {
        let task = Process()
        task.executableURL = URL(fileURLWithPath: "/bin/sleep")
        task.arguments = ["60"]
        try task.run()

        let status = WorkerClient.exitStatus(of: task, waitSeconds: 5)

        XCTAssertNotNil(status, "terminating a live child must still yield its status")
        XCTAssertFalse(task.isRunning, "no live child may outlive the call")
    }
}

/// Integration guard: drives the real worker binary and checks that the daemon side keeps no
/// residue. Skipped when the worker has not been built.
final class WorkerClientIntegrationTests: XCTestCase {
    private var workerPath: String? {
        let repoRoot = URL(fileURLWithPath: #filePath)
            .deletingLastPathComponent()  // SpeechRuntimeCoreTests
            .deletingLastPathComponent()  // Tests
            .deletingLastPathComponent()  // daemon
            .deletingLastPathComponent()  // runtime
        let candidate = repoRoot
            .appendingPathComponent("worker/target/debug/sr-worker").path
        return FileManager.default.isExecutableFile(atPath: candidate) ? candidate : nil
    }

    func testManyRepliesLeaveNoPendingRepliesOrLogNoise() async throws {
        let path = try XCTUnwrap(workerPath, "build runtime/worker first (cargo build)")
        let lines = Box<[String]>([])
        let client = WorkerClient(workerPath: path) { line in lines.mutate { $0.append(line) } }
        try await client.start()
        defer { Task { await client.stop() } }

        for _ in 0..<500 {
            let reply = try await client.ping()
            XCTAssertEqual(reply["pong"] as? Bool, true)
        }
        let internals = await client.internals()
        XCTAssertEqual(internals.pendingReplies, 0, "every reply must drain its waiter")
        XCTAssertEqual(internals.eventWaiters, 0)
        XCTAssertEqual(internals.sessionBuffers, 0)
        let restarts = await client.restartCount
        XCTAssertEqual(restarts, 0)
        let noise = lines.current.filter { $0.contains("without waiter") || $0.contains("unparsable") }
        XCTAssertTrue(noise.isEmpty, "unexpected worker chatter: \(noise)")
    }

    func testKilledWorkerIsRestartedAndTheClientKeepsServing() async throws {
        let path = try XCTUnwrap(workerPath, "build runtime/worker first (cargo build)")
        let client = WorkerClient(workerPath: path) { _ in }
        try await client.start()
        defer { Task { await client.stop() } }

        let pidBefore = await client.pid
        let firstPID = try XCTUnwrap(pidBefore, "the worker must have a pid")
        let firstPong = try await client.ping()["pong"] as? Bool
        XCTAssertEqual(firstPong, true)

        kill(firstPID, SIGKILL)
        var pid = firstPID
        for _ in 0..<100 where pid == firstPID {
            try await Task.sleep(nanoseconds: 50_000_000)
            pid = await client.pid ?? firstPID
        }

        let restarts = await client.restartCount
        let lastExit = await client.workerLastExit
        let pong = try await client.ping()["pong"] as? Bool
        XCTAssertNotEqual(pid, firstPID, "the supervisor must bring up a fresh worker")
        XCTAssertEqual(restarts, 1)
        XCTAssertEqual(lastExit, SIGKILL, "the crash is reported, not hidden")
        XCTAssertEqual(pong, true, "the new generation must serve")
    }

    func testLateEventAfterForgetIsDropped() async throws {
        let path = try XCTUnwrap(workerPath, "build runtime/worker first (cargo build)")
        let lines = Box<[String]>([])
        let client = WorkerClient(workerPath: path) { line in lines.mutate { $0.append(line) } }
        try await client.start()
        defer { Task { await client.stop() } }

        await client.forgetSession("ghost")
        // Give the worker a moment to emit anything it might still owe for that id, then poll.
        _ = await client.pollEvents(session: "ghost", since: 0, timeout: 0.2)
        let internals = await client.internals()
        XCTAssertEqual(internals.sessionBuffers, 0, "a forgotten session must not be resurrected")
        XCTAssertEqual(internals.retiredSessions, 1)
    }
}
