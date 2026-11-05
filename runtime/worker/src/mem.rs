//! Process-local memory stats for the `stats` reply: the daemon cannot read
//! another process's task_info without root, so the worker reports its own.
//!
//! Struct layouts mirror the macOS SDK headers verbatim (`mach/task_info.h`
//! `task_vm_info` rev7 and `sys/resource.h` `rusage_info_v6`), declared
//! locally so no new crate is needed. A value the syscalls did not produce is
//! `None` (serialized as `null`), never a fabricated 0. Metrics are reported
//! exactly as the kernel produced them — never summed.

use serde_json::{json, Value};

#[derive(Debug, Clone, Copy, Default, PartialEq)]
pub struct MemoryStats {
    pub resident_bytes: Option<u64>,
    pub phys_footprint_bytes: Option<u64>,
    pub compressed_bytes: Option<u64>,
    pub neural_bytes: Option<u64>,
    pub neural_lifetime_max_bytes: Option<u64>,
    pub neural_interval_max_bytes: Option<u64>,
    pub phys_footprint_lifetime_max_bytes: Option<u64>,
    pub phys_footprint_interval_max_bytes: Option<u64>,
    pub task_vm_info_ok: bool,
    pub rusage_ok: bool,
}

impl MemoryStats {
    /// The `memory` object of the stats reply; unavailable values are `null`.
    pub fn to_json(&self) -> Value {
        fn opt(v: Option<u64>) -> Value {
            v.map(Value::from).unwrap_or(Value::Null)
        }
        json!({
            "resident_bytes": opt(self.resident_bytes),
            "phys_footprint_bytes": opt(self.phys_footprint_bytes),
            "compressed_bytes": opt(self.compressed_bytes),
            "neural_bytes": opt(self.neural_bytes),
            "neural_lifetime_max_bytes": opt(self.neural_lifetime_max_bytes),
            "neural_interval_max_bytes": opt(self.neural_interval_max_bytes),
            "phys_footprint_lifetime_max_bytes": opt(self.phys_footprint_lifetime_max_bytes),
            "phys_footprint_interval_max_bytes": opt(self.phys_footprint_interval_max_bytes),
            "task_vm_info_ok": self.task_vm_info_ok,
            "rusage_ok": self.rusage_ok,
        })
    }
}

#[cfg(target_os = "macos")]
mod imp {
    use super::MemoryStats;

    type MachPort = u32; // mach_port_t
    type NaturalT = u32; // natural_t
    type MsgTypeNumber = u32; // mach_msg_type_number_t

    const TASK_VM_INFO: u32 = 22;
    const KERN_SUCCESS: i32 = 0;
    const RUSAGE_INFO_V6: u32 = 6;

    extern "C" {
        fn task_info(
            target_task: MachPort,
            flavor: u32,
            task_info_out: *mut std::ffi::c_void,
            task_info_out_cnt: *mut MsgTypeNumber,
        ) -> i32;
        fn mach_task_self() -> MachPort;
        fn proc_pid_rusage(pid: i32, flavor: u32, buffer: *mut std::ffi::c_void) -> i32;
        fn getpid() -> i32;
    }

    /// `task_vm_info` (rev7) from `mach/task_info.h`. Field-for-field.
    #[repr(C)]
    #[allow(clippy::struct_field_names)]
    struct TaskVmInfo {
        virtual_size: u64,
        region_count: i32,
        page_size: i32,
        resident_size: u64,
        resident_size_peak: u64,
        device: u64,
        device_peak: u64,
        internal: u64,
        internal_peak: u64,
        external: u64,
        external_peak: u64,
        reusable: u64,
        reusable_peak: u64,
        purgeable_volatile_pmap: u64,
        purgeable_volatile_resident: u64,
        purgeable_volatile_virtual: u64,
        compressed: u64,
        compressed_peak: u64,
        compressed_lifetime: u64,
        phys_footprint: u64,
        min_address: u64,
        max_address: u64,
        ledger_phys_footprint_peak: i64,
        ledger_purgeable_nonvolatile: i64,
        ledger_purgeable_novolatile_compressed: i64,
        ledger_purgeable_volatile: i64,
        ledger_purgeable_volatile_compressed: i64,
        ledger_tag_network_nonvolatile: i64,
        ledger_tag_network_nonvolatile_compressed: i64,
        ledger_tag_network_volatile: i64,
        ledger_tag_network_volatile_compressed: i64,
        ledger_tag_media_footprint: i64,
        ledger_tag_media_footprint_compressed: i64,
        ledger_tag_media_nofootprint: i64,
        ledger_tag_media_nofootprint_compressed: i64,
        ledger_tag_graphics_footprint: i64,
        ledger_tag_graphics_footprint_compressed: i64,
        ledger_tag_graphics_nofootprint: i64,
        ledger_tag_graphics_nofootprint_compressed: i64,
        ledger_tag_neural_footprint: i64,
        ledger_tag_neural_footprint_compressed: i64,
        ledger_tag_neural_nofootprint: i64,
        ledger_tag_neural_nofootprint_compressed: i64,
        limit_bytes_remaining: u64,
        decompressions: i32,
        ledger_swapins: i64,
        ledger_tag_neural_nofootprint_total: i64,
        ledger_tag_neural_nofootprint_peak: i64,
    }

    /// `rusage_info_v6` from `sys/resource.h`. Field-for-field.
    #[repr(C)]
    struct RusageInfoV6 {
        ri_uuid: [u8; 16],
        ri_user_time: u64,
        ri_system_time: u64,
        ri_pkg_idle_wkups: u64,
        ri_interrupt_wkups: u64,
        ri_pageins: u64,
        ri_wired_size: u64,
        ri_resident_size: u64,
        ri_phys_footprint: u64,
        ri_proc_start_abstime: u64,
        ri_proc_exit_abstime: u64,
        ri_child_user_time: u64,
        ri_child_system_time: u64,
        ri_child_pkg_idle_wkups: u64,
        ri_child_interrupt_wkups: u64,
        ri_child_pageins: u64,
        ri_child_elapsed_abstime: u64,
        ri_diskio_bytesread: u64,
        ri_diskio_byteswritten: u64,
        ri_cpu_time_qos_default: u64,
        ri_cpu_time_qos_maintenance: u64,
        ri_cpu_time_qos_background: u64,
        ri_cpu_time_qos_utility: u64,
        ri_cpu_time_qos_legacy: u64,
        ri_cpu_time_qos_user_initiated: u64,
        ri_cpu_time_qos_user_interactive: u64,
        ri_billed_system_time: u64,
        ri_serviced_system_time: u64,
        ri_logical_writes: u64,
        ri_lifetime_max_phys_footprint: u64,
        ri_instructions: u64,
        ri_cycles: u64,
        ri_billed_energy: u64,
        ri_serviced_energy: u64,
        ri_interval_max_phys_footprint: u64,
        ri_runnable_time: u64,
        ri_flags: u64,
        ri_user_ptime: u64,
        ri_system_ptime: u64,
        ri_pinstructions: u64,
        ri_pcycles: u64,
        ri_energy_nj: u64,
        ri_penergy_nj: u64,
        ri_secure_time_in_system: u64,
        ri_secure_ptime: u64,
        ri_neural_footprint: u64,
        ri_lifetime_max_neural_footprint: u64,
        ri_interval_max_neural_footprint: u64,
        ri_conclave_footprint: u64,
        ri_page_wait_time_mach: u64,
        ri_page_cache_hits: u64,
        ri_reserved: [u64; 6],
    }

    /// Reads this process's memory metrics. Infallible: on syscall failure the
    /// affected fields stay `None` and the matching `*_ok` flag is false.
    pub fn read_memory() -> MemoryStats {
        let mut stats = MemoryStats::default();

        let mut info: TaskVmInfo = unsafe { std::mem::zeroed() };
        let mut count =
            (std::mem::size_of::<TaskVmInfo>() / std::mem::size_of::<NaturalT>()) as MsgTypeNumber;
        let kr = unsafe {
            task_info(
                mach_task_self(),
                TASK_VM_INFO,
                &mut info as *mut TaskVmInfo as *mut std::ffi::c_void,
                &mut count,
            )
        };
        if kr == KERN_SUCCESS {
            stats.task_vm_info_ok = true;
            stats.resident_bytes = Some(info.resident_size);
            stats.phys_footprint_bytes = Some(info.phys_footprint);
            stats.compressed_bytes = Some(info.compressed);
            stats.neural_bytes = Some(info.ledger_tag_neural_footprint.max(0) as u64);
        }

        let mut ri: RusageInfoV6 = unsafe { std::mem::zeroed() };
        let rv = unsafe {
            proc_pid_rusage(
                getpid(),
                RUSAGE_INFO_V6,
                &mut ri as *mut RusageInfoV6 as *mut std::ffi::c_void,
            )
        };
        if rv == 0 {
            stats.rusage_ok = true;
            stats.neural_lifetime_max_bytes = Some(ri.ri_lifetime_max_neural_footprint);
            stats.neural_interval_max_bytes = Some(ri.ri_interval_max_neural_footprint);
            stats.phys_footprint_lifetime_max_bytes = Some(ri.ri_lifetime_max_phys_footprint);
            stats.phys_footprint_interval_max_bytes = Some(ri.ri_interval_max_phys_footprint);
        }

        stats
    }
}

#[cfg(target_os = "macos")]
pub use imp::read_memory;

#[cfg(not(target_os = "macos"))]
pub fn read_memory() -> MemoryStats {
    MemoryStats::default()
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn unavailable_values_are_null_never_zero() {
        let stats = MemoryStats::default();
        let value = stats.to_json();
        assert_eq!(value["resident_bytes"], Value::Null);
        assert_eq!(value["phys_footprint_bytes"], Value::Null);
        assert_eq!(value["neural_bytes"], Value::Null);
        assert_eq!(value["task_vm_info_ok"], false);
        assert_eq!(value["rusage_ok"], false);
    }

    #[test]
    #[cfg(target_os = "macos")]
    fn real_process_reports_non_null_resident_and_footprint() {
        let stats = read_memory();
        assert!(stats.task_vm_info_ok, "task_info must succeed on macOS");
        assert!(stats.resident_bytes.is_some_and(|v| v > 0));
        assert!(stats.phys_footprint_bytes.is_some_and(|v| v > 0));
    }
}
