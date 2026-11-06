#include "ProcessMetrics.h"
#if defined(__APPLE__)
#include <libproc.h>
#include <sys/resource.h>
#endif
sr_process_sample sr_sample_process(int32_t pid) {
    sr_process_sample result = {0};
#if defined(__APPLE__)
    if (pid <= 0) return result;
    struct rusage_info_v4 usage = {0};
    if (proc_pid_rusage(pid, RUSAGE_INFO_V4, (rusage_info_t *)&usage) != 0) return result;
    result.available = true;
    result.cpu_ns = usage.ri_user_time + usage.ri_system_time;
    result.started = usage.ri_proc_start_abstime;
    result.resident_bytes = usage.ri_resident_size;
    result.footprint_bytes = usage.ri_phys_footprint;
#else
    (void)pid;
#endif
    return result;
}
