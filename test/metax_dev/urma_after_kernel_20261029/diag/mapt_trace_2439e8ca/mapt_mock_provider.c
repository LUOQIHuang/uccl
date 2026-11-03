#include <stdint.h>
#include "ummu_api.h"
int mock_call(uint32_t tid) {
    struct ummu_seg_attr attr = {0};
    return ummu_grant(tid, (void *)(uintptr_t)0xa04c12000ULL, 4096, MAPT_PERM_ATOMIC_RW, &attr);
}
