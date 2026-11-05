/*
 * memory_probe_macos.c — per-process macOS memory / neural-footprint probe.
 *
 * Reports, separately, without aggregation:
 *   - task_vm_info (TASK_VM_INFO) fields from the probed task's ledger:
 *     resident_size, phys_footprint, compressed,
 *     ledger_tag_neural_footprint(_compressed),
 *     ledger_tag_neural_nofootprint(_compressed|_total|_peak)
 *   - proc_pid_rusage RUSAGE_INFO_V6 fields (falls back to V4):
 *     ri_physical_footprint_bytes, ri_resident_size,
 *     ri_neural_footprint, ri_lifetime_max_neural_footprint,
 *     ri_interval_max_neural_footprint
 *
 * Unknown or unavailable data is emitted as null with a status string,
 * never as 0. All byte fields carry "unit":"bytes" and "source" naming
 * the API + SDK that produced them.
 *
 * Build: cc -O2 -Wall -Wextra -o tools/memory_probe/memory_probe_macos \
 *           tools/memory_probe/memory_probe_macos.c
 *
 * Visibility boundary: this tool sees only counters attributed to the
 * probed process by the kernel. It does NOT measure whole-system ANE
 * usage (other processes, system services, ANE firmware caching).
 * When --pid is given, only same-user PIDs are readable without root;
 * pid 1 yields permission_denied/unavailable, not a crash.
 */

#include <errno.h>
#include <inttypes.h>
#include <mach/mach.h>
#include <stdbool.h>
#include <stdint.h>
#include <stdio.h>
#include <sys/resource.h>
#include <stdlib.h>
#include <string.h>
#include <sys/proc_info.h>
#include <libproc.h>
#include <unistd.h>

/* rusage_info_v6 availability: RUSAGE_INFO_V6 and struct rusage_info_v6
 * come from <sys/resource.h>; presence is compile-time (SDK headers). */
#if defined(RUSAGE_INFO_V6)
#define HAS_V6_NEURAL 1
#else
#define HAS_V6_NEURAL 0
#endif

/* ---- JSON output helpers -------------------------------------------- */

static void json_kv_str(const char *key, const char *value) {
    printf("\"%s\": \"%s\"", key, value);
}

static void json_kv_u64(const char *key, uint64_t value) {
    printf("\"%s\": %" PRIu64, key, value);
}

static void json_kv_null(const char *key) {
    printf("\"%s\": null", key);
}


/* ---- task_vm_info sampling ------------------------------------------ */

typedef struct {
    bool   supported;
    int    status;        /* KERN_SUCCESS / other */
    mach_msg_type_number_t count;
    struct task_vm_info tvi;
} tvi_result;

static const char *tvi_status_string(const tvi_result *r) {
    if (r->status == KERN_SUCCESS) {
        if (r->count >= TASK_VM_INFO_COUNT) return "ok";
        /* Kernel returned a shorter struct than this SDK's layout:
         * fields beyond task_vm_info_count are not safe to read. */
        return "unsupported";
    }
    if (r->status == KERN_FAILURE) return "unavailable";
    return "unavailable";
}

/*
 * Sample TASK_VM_INFO for the current process only (mach_task_self()).
 * For a foreign pid we have no task port and must not attempt
 * task_for_pid (requires root / entitlement); rusage covers that path.
 */
static tvi_result sample_task_vm_info(void) {
    tvi_result r;
    memset(&r, 0, sizeof r);
    r.status = KERN_INVALID_ARGUMENT; /* overwritten on success */
    mach_msg_type_number_t count = TASK_VM_INFO_COUNT;
    kern_return_t kr = task_info(mach_task_self(), TASK_VM_INFO,
                                 (task_info_t)&r.tvi, &count);
    r.status = kr;
    r.count = count;
    r.supported = (kr == KERN_SUCCESS);
    return r;
}

/*
 * Ledger fields live at the tail of struct task_vm_info; their presence
 * is decided by the returned count vs the field's byte offset in the
 * SDK layout. We compute offsets at runtime from a zeroed struct so
 * truncated kernels are reported as unsupported, never read OOB.
 */
static bool tvi_field_present(const tvi_result *r, size_t field_offset,
                              size_t field_size) {
    if (r->status != KERN_SUCCESS) return false;
    return (field_offset + field_size) <= (size_t)r->count * sizeof(integer_t);
}

#define TVI_FIELD(res, fname) \
    tvi_field_present((res), offsetof(struct task_vm_info, fname), \
                      sizeof(((struct task_vm_info *)0)->fname))

/* One field object inside the `fields` array; null when the kernel did not produce it.
 * Every field is its own array element: a flat object would repeat the "field" key and
 * a JSON parser would silently keep only the last value. */
static void emit_tvi_bytes(const tvi_result *r, bool *first, const char *name,
                           bool present, uint64_t value) {
    printf("%s    { ", *first ? "" : ",\n    ");
    *first = false;
    json_kv_str("field", name);
    if (present) {
        printf(", ");
        json_kv_u64("value", value);
        printf(", ");
        json_kv_str("unit", "bytes");
        printf(", ");
        json_kv_str("source", "task_info(TASK_VM_INFO) / SDK task_vm_info ledger");
        printf(", ");
        json_kv_str("kind", "current");
    } else {
        printf(", ");
        json_kv_null("value");
        printf(", ");
        json_kv_str("status",
            r->status != KERN_SUCCESS ? "unavailable" : "unsupported");
        printf(", ");
        json_kv_str("reason",
            "returned task_info_count does not cover this field (truncated struct) or call failed");
    }
    printf(" }");
}

static void emit_task_vm_info(const tvi_result *r) {
    const char *st = tvi_status_string(r);
    printf("  \"task_vm_info\": {\n    ");
    json_kv_str("status", st);
    printf(",\n    ");
    json_kv_u64("task_info_count", r->count);
    printf(",\n    ");
    json_kv_u64("task_vm_info_count_sdk", TASK_VM_INFO_COUNT);
    printf(",\n    \"fields\": [\n");

    bool first = true;
    emit_tvi_bytes(r, &first, "resident_size",
                   TVI_FIELD(r, resident_size), r->tvi.resident_size);
    emit_tvi_bytes(r, &first, "phys_footprint",
                   TVI_FIELD(r, phys_footprint), r->tvi.phys_footprint);
    emit_tvi_bytes(r, &first, "compressed",
                   TVI_FIELD(r, compressed), r->tvi.compressed);
    emit_tvi_bytes(r, &first, "ledger_tag_neural_footprint",
                   TVI_FIELD(r, ledger_tag_neural_footprint),
                   r->tvi.ledger_tag_neural_footprint);
    emit_tvi_bytes(r, &first, "ledger_tag_neural_footprint_compressed",
                   TVI_FIELD(r, ledger_tag_neural_footprint_compressed),
                   r->tvi.ledger_tag_neural_footprint_compressed);
    emit_tvi_bytes(r, &first, "ledger_tag_neural_nofootprint",
                   TVI_FIELD(r, ledger_tag_neural_nofootprint),
                   r->tvi.ledger_tag_neural_nofootprint);
    emit_tvi_bytes(r, &first, "ledger_tag_neural_nofootprint_compressed",
                   TVI_FIELD(r, ledger_tag_neural_nofootprint_compressed),
                   r->tvi.ledger_tag_neural_nofootprint_compressed);
    emit_tvi_bytes(r, &first, "ledger_tag_neural_nofootprint_total",
                   TVI_FIELD(r, ledger_tag_neural_nofootprint_total),
                   r->tvi.ledger_tag_neural_nofootprint_total);
    printf("\n    ]\n  }");
}

/* task_vm_info on this SDK ends at ledger_tag_neural_nofootprint_peak;
 * the *_peak field is the historical maximum. */
static void emit_task_vm_info_peak(const tvi_result *r) {
    printf(",\n  \"task_vm_info_peaks\": {\n    \"fields\": [\n      { ");
    json_kv_str("field", "ledger_tag_neural_nofootprint_peak");
    if (TVI_FIELD(r, ledger_tag_neural_nofootprint_peak)) {
        printf(", ");
        json_kv_u64("value", r->tvi.ledger_tag_neural_nofootprint_peak);
        printf(", ");
        json_kv_str("unit", "bytes");
        printf(", ");
        json_kv_str("source",
            "task_info(TASK_VM_INFO) / SDK task_vm_info ledger");
        printf(", ");
        json_kv_str("kind", "peak (historical maximum, never decreases)");
    } else {
        printf(", ");
        json_kv_null("value");
        printf(", ");
        json_kv_str("status", "unsupported");
        printf(", ");
        json_kv_str("reason",
            "returned task_info_count does not cover this field (truncated struct)");
    }
    printf(" }\n    ]\n  }");
}

/* ---- proc_pid_rusage sampling --------------------------------------- */

typedef struct {
    bool v6;
    bool ok;
    const char *status; /* ok | permission_denied | unsupported | unavailable */
    struct rusage_info_v6 ri6; /* also used for v4 fields (prefix-compatible) */
} rusage_result;

static rusage_result sample_rusage(pid_t pid) {
    rusage_result r;
    memset(&r, 0, sizeof r);
    r.status = "unavailable";

#if HAS_V6_NEURAL
    {
        int rc = proc_pid_rusage(pid, RUSAGE_INFO_V6, (rusage_info_t)&r.ri6);
        if (rc == 0) {
            r.v6 = true;
            r.ok = true;
            r.status = "ok";
            return r;
        }
        /* fall through to V4 attempt so older kernels still report memory */
    }
#endif
    {
        struct rusage_info_v4 ri4;
        memset(&ri4, 0, sizeof ri4);
        int rc = proc_pid_rusage(pid, RUSAGE_INFO_V4, (rusage_info_t)&ri4);
        if (rc == 0) {
            /* v6 fields that exist in v4: prefix layout is guaranteed by
             * <sys/resource.h> (rusage_info_v4 is a prefix of v5/v6). */
            r.ri6.ri_phys_footprint = ri4.ri_phys_footprint;
            r.ri6.ri_resident_size = ri4.ri_resident_size;
            r.v6 = false;
            r.ok = true;
            r.status = "ok";
            return r;
        }
        if (errno == EPERM || errno == EACCES) {
            r.status = "permission_denied";
        } else {
            r.status = "unavailable";
        }
    }
    return r;
}

static void emit_rusage_field(const rusage_result *r, bool *first, const char *name,
                              bool v6only, uint64_t value) {
    printf("%s    { ", *first ? "" : ",\n    ");
    *first = false;
    json_kv_str("field", name);
    if (r->ok && (!v6only || r->v6)) {
        printf(", ");
        json_kv_u64("value", value);
        printf(", ");
        json_kv_str("unit", "bytes");
        printf(", ");
        json_kv_str("source", r->v6
            ? "proc_pid_rusage(RUSAGE_INFO_V6) / SDK rusage_info_v6"
            : "proc_pid_rusage(RUSAGE_INFO_V4) / SDK rusage_info_v4 (prefix layout)");
        printf(", ");
        json_kv_str("kind", "current");
    } else {
        printf(", ");
        json_kv_null("value");
        printf(", ");
        if (r->status && strcmp(r->status, "ok") == 0) {
            json_kv_str("status", "unsupported");
            printf(", ");
            json_kv_str("reason", "RUSAGE_INFO_V6 unavailable in this SDK/kernel; V4 fallback has no neural fields");
        } else {
            json_kv_str("status", r->status);
            printf(", ");
            json_kv_str("reason", "proc_pid_rusage failed for this pid");
        }
    }
    printf(" }");
}

static void emit_rusage_peak(const rusage_result *r, bool *first, const char *name,
                             uint64_t value, const char *kind) {
    printf("%s    { ", *first ? "" : ",\n    ");
    *first = false;
    json_kv_str("field", name);
    if (r->ok && r->v6) {
        printf(", ");
        json_kv_u64("value", value);
        printf(", ");
        json_kv_str("unit", "bytes");
        printf(", ");
        json_kv_str("source",
            "proc_pid_rusage(RUSAGE_INFO_V6) / SDK rusage_info_v6");
        printf(", ");
        json_kv_str("kind", kind);
    } else {
        printf(", ");
        json_kv_null("value");
        printf(", ");
        if (r->status && strcmp(r->status, "ok") == 0) {
            json_kv_str("status", "unsupported");
            printf(", ");
            json_kv_str("reason", "RUSAGE_INFO_V6 unavailable; maxima are V6-only fields");
        } else {
            json_kv_str("status", r->status);
            printf(", ");
            json_kv_str("reason", "proc_pid_rusage failed for this pid");
        }
    }
    printf(" }");
}

static void emit_rusage(const rusage_result *r) {
    printf("  \"proc_pid_rusage\": {\n    ");
    json_kv_str("api_level", r->v6 ? "RUSAGE_INFO_V6" : "RUSAGE_INFO_V4 (fallback)");
    printf(",\n    ");
    json_kv_str("status", r->status);
    printf(",\n    \"fields\": [\n");

    bool first = true;
    emit_rusage_field(r, &first, "ri_physical_footprint_bytes", false,
                      r->ri6.ri_phys_footprint);
    emit_rusage_field(r, &first, "ri_resident_size", false, r->ri6.ri_resident_size);
#if HAS_V6_NEURAL
    emit_rusage_field(r, &first, "ri_neural_footprint", true,
                      r->ri6.ri_neural_footprint);
    emit_rusage_peak(r, &first, "ri_lifetime_max_neural_footprint",
                     r->ri6.ri_lifetime_max_neural_footprint,
                     "lifetime maximum (since process start, never decreases)");
    emit_rusage_peak(r, &first, "ri_interval_max_neural_footprint",
                     r->ri6.ri_interval_max_neural_footprint,
                     "interval maximum (recent window, may be reset by the kernel)");
#else
    emit_rusage_field(r, &first, "ri_neural_footprint", true, 0);
    emit_rusage_peak(r, &first, "ri_lifetime_max_neural_footprint", 0, "lifetime maximum");
    emit_rusage_peak(r, &first, "ri_interval_max_neural_footprint", 0, "interval maximum");
#endif
    printf("\n    ]\n  }");
}

/* ---- top-level ------------------------------------------------------- */

static void usage(FILE *out, const char *prog) {
    fprintf(out,
        "usage: %s [--pid N] [--samples N] [--interval-ms M] [--json]\n"
        "  default: probe own process, 1 sample, JSON to stdout\n",
        prog);
}

int main(int argc, char **argv) {
    pid_t pid = getpid();
    long samples = 1;
    long interval_ms = 1000;
    bool json = true;
    bool self = true;

    for (int i = 1; i < argc; i++) {
        if (strcmp(argv[i], "--pid") == 0 && i + 1 < argc) {
            char *end = NULL;
            long v = strtol(argv[++i], &end, 10);
            if (end && *end == '\0' && v > 0) {
                pid = (pid_t)v;
                self = false;
            } else {
                usage(stderr, argv[0]);
                return 2;
            }
        } else if (strcmp(argv[i], "--samples") == 0 && i + 1 < argc) {
            char *end = NULL;
            long v = strtol(argv[++i], &end, 10);
            if (end && *end == '\0' && v >= 1 && v <= 1000) samples = v;
            else { usage(stderr, argv[0]); return 2; }
        } else if (strcmp(argv[i], "--interval-ms") == 0 && i + 1 < argc) {
            char *end = NULL;
            long v = strtol(argv[++i], &end, 10);
            if (end && *end == '\0' && v >= 1 && v <= 60000) interval_ms = v;
            else { usage(stderr, argv[0]); return 2; }
        } else if (strcmp(argv[i], "--json") == 0) {
            json = true;
        } else {
            usage(stderr, argv[0]);
            return 2;
        }
    }
    (void)json; /* JSON is the only output format; flag kept for CLI compat */

    printf("{\n  ");
    json_kv_str("schema", "memory_probe_macos/1");
    printf(",\n  ");
    json_kv_u64("pid", (uint64_t)pid);
    printf(",\n  ");
    json_kv_str("target", self ? "self (mach_task_self()/getpid())"
                               : "external pid (proc_pid_rusage only)");
    printf(",\n  ");
    json_kv_str("unit_policy", "all memory values are bytes; never aggregated; unavailable data is null, not 0");
    printf(",\n  ");
    json_kv_str("visibility_boundary",
        "counters are attributed to the probed process by the kernel; "
        "whole-system ANE usage (other processes, system services, "
        "ANE firmware) is NOT visible here");
    printf(",\n  ");

    json_kv_u64("samples", (uint64_t)samples);
    printf(",\n  \"samples_data\": [");

    for (long s = 0; s < samples; s++) {
        if (s > 0) {
            usleep((useconds_t)(interval_ms * 1000));
            printf(",");
        }
        printf("\n  {");
        if (self) {
            tvi_result tvi = sample_task_vm_info();
            emit_task_vm_info(&tvi);
            emit_task_vm_info_peak(&tvi);
        } else {
            printf("\n  \"task_vm_info\": {\n    ");
            json_kv_str("status", "unsupported");
            printf(",\n    ");
            json_kv_str("reason", "external pid: no task port without task_for_pid (requires root); only proc_pid_rusage is probed");
            printf("\n  }");
        }
        rusage_result ru = sample_rusage(pid);
        printf(",\n");
        emit_rusage(&ru);
        printf("\n  }");
    }

    printf("\n  ]\n}\n");
    return 0;
}
