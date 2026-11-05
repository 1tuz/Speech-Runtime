import Darwin
import Foundation

/// Per-process memory accounting, read from the kernel for *this* process.
///
/// Cross-process sampling of another pid needs `task_for_pid`, i.e. root, which the daemon does
/// not have; so every process reports on itself and the caller adds the numbers up per process
/// instead of trying to attribute a single RSS to the whole stack.
///
/// Units are bytes throughout. A field the kernel refuses to answer stays `nil` — never 0 — so a
/// missing measurement cannot be read as "no memory used".
public struct MemoryReport: Sendable {
    public var taskVMInfoOK: Bool
    public var residentBytes: UInt64?
    public var physFootprintBytes: UInt64?
    public var compressedBytes: UInt64?
    public var neuralBytes: UInt64?
    public var rusageOK: Bool
    public var rusageAPIName: String
    public var neuralLifetimeMaxBytes: UInt64?
    public var neuralIntervalMaxBytes: UInt64?
    public var physFootprintLifetimeMaxBytes: UInt64?
    public var physFootprintIntervalMaxBytes: UInt64?

    public static func capture() -> MemoryReport {
        var report = MemoryReport(
            taskVMInfoOK: false,
            residentBytes: nil,
            physFootprintBytes: nil,
            compressedBytes: nil,
            neuralBytes: nil,
            rusageOK: false,
            rusageAPIName: "RUSAGE_INFO_V6",
            neuralLifetimeMaxBytes: nil,
            neuralIntervalMaxBytes: nil,
            physFootprintLifetimeMaxBytes: nil,
            physFootprintIntervalMaxBytes: nil
        )

        var info = task_vm_info_data_t()
        var count = mach_msg_type_number_t(MemoryLayout<task_vm_info_data_t>.size / MemoryLayout<integer_t>.size)
        let vmResult = withUnsafeMutablePointer(to: &info) { pointer -> kern_return_t in
            pointer.withMemoryRebound(to: integer_t.self, capacity: Int(count)) { rebound in
                task_info(mach_task_self_, task_flavor_t(TASK_VM_INFO), rebound, &count)
            }
        }
        if vmResult == KERN_SUCCESS {
            report.taskVMInfoOK = true
            report.residentBytes = UInt64(info.resident_size)
            report.physFootprintBytes = UInt64(info.phys_footprint)
            report.compressedBytes = UInt64(info.compressed)
            report.neuralBytes = UInt64(info.ledger_tag_neural_footprint)
        }

        var usage = rusage_info_v6()
        let rusageResult = withUnsafeMutablePointer(to: &usage) { pointer -> Int32 in
            pointer.withMemoryRebound(to: rusage_info_t?.self, capacity: 1) { rebound in
                proc_pid_rusage(getpid(), RUSAGE_INFO_V6, rebound)
            }
        }
        if rusageResult == 0 {
            report.rusageOK = true
            report.physFootprintLifetimeMaxBytes = usage.ri_lifetime_max_phys_footprint
            report.physFootprintIntervalMaxBytes = usage.ri_interval_max_phys_footprint
            report.neuralLifetimeMaxBytes = usage.ri_lifetime_max_neural_footprint
            report.neuralIntervalMaxBytes = usage.ri_interval_max_neural_footprint
            if report.neuralBytes == nil { report.neuralBytes = usage.ri_neural_footprint }
            if report.residentBytes == nil { report.residentBytes = usage.ri_resident_size }
            if report.physFootprintBytes == nil { report.physFootprintBytes = usage.ri_phys_footprint }
        }
        return report
    }

    /// JSON shape for `GET /health`. Keys are stable; `null` means "kernel did not answer".
    public var json: [String: Any] {
        func box(_ value: UInt64?) -> Any {
            value.map { $0 as Any } ?? NSNull()
        }
        return [
            "pid": Int(ProcessInfo.processInfo.processIdentifier),
            "task_vm_info_ok": taskVMInfoOK,
            "rusage_ok": rusageOK,
            "rusage_api": rusageAPIName,
            "resident_bytes": box(residentBytes),
            "phys_footprint_bytes": box(physFootprintBytes),
            "compressed_bytes": box(compressedBytes),
            "neural_bytes": box(neuralBytes),
            "neural_lifetime_max_bytes": box(neuralLifetimeMaxBytes),
            "neural_interval_max_bytes": box(neuralIntervalMaxBytes),
            "phys_footprint_lifetime_max_bytes": box(physFootprintLifetimeMaxBytes),
            "phys_footprint_interval_max_bytes": box(physFootprintIntervalMaxBytes),
        ]
    }
}
