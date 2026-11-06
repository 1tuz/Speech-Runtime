#ifndef SR_PROCESS_METRICS_H
#define SR_PROCESS_METRICS_H
#include <stdint.h>
#include <stdbool.h>
typedef struct {
    bool available;
    uint64_t cpu_ns;
    uint64_t started;
    uint64_t resident_bytes;
    uint64_t footprint_bytes;
} sr_process_sample;
sr_process_sample sr_sample_process(int32_t pid);
#endif
