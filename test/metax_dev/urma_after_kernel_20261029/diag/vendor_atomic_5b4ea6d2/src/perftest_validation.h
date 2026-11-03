/*
 * SPDX-License-Identifier: MIT
 * Copyright (c) Huawei Technologies Co., Ltd. 2024-2026. All rights reserved.
 *
 * Buffer fill/check helpers for host and CUDA device memory.
 * check_* returns mismatched byte count (0 = PASS); GPU paths use H2D/D2H.
 *
 * Compile-time gate for diagnostic validation / gdr-verify on the hot path:
 *   cmake -DPERFTEST_ENABLE_GPU_DEBUG=ON  (urma_perftest default)
 *   or -DPERFTEST_ENABLE_GPU_DEBUG=OFF for latency numbers without D2H dump.
 * Helpers in this .c are always linked; write_lat call sites are #if-gated.
 */

#ifndef PERFTEST_VALIDATION_H
#define PERFTEST_VALIDATION_H

#include <stdbool.h>
#include <stddef.h>
#include <stdint.h>

#ifndef PERFTEST_ENABLE_GPU_DEBUG
#define PERFTEST_ENABLE_GPU_DEBUG 0
#endif

#ifdef __cplusplus
extern "C" {
#endif

void   perftest_validation_fill_host (void *buf,           size_t size, uint32_t seed);
size_t perftest_validation_check_host(const void *buf,     size_t size, uint32_t seed);

int    perftest_validation_fill_gpu (void *dptr,           size_t size, uint32_t seed);
long   perftest_validation_check_gpu(void *dptr,           size_t size, uint32_t seed);

int  perftest_validation_fill (void *addr, size_t size, uint32_t seed, bool is_device_va);
long perftest_validation_check(void *addr, size_t size, uint32_t seed, bool is_device_va);

/* MetaX mcMemcpy if loaded, else CUDA cuMemcpy. Used by gdr-verify. */
int perftest_validation_memcpy_h2d(void *dev, const void *host, size_t n);
int perftest_validation_memcpy_d2h(void *host, void *dev, size_t n);

#define PERFTEST_WRITE_LAT_VALID_SEED  (0x5542444du) /* 'UBDM' */

/* Asymmetric host->GPU write eyeball check (plant magic / dump recv). */
#define PERFTEST_GDR_EYE_MAGIC       "test GDR write"
#define PERFTEST_GDR_EYE_INIT_MARK   "GPU_RECV_UNWRITTEN"

const char *perftest_gdr_eye_magic(void);
size_t      perftest_gdr_eye_min_size(void);

int  perftest_gdr_eye_plant_host(void *send_base, uint32_t size);
int  perftest_gdr_eye_plant(void *send_base, uint32_t size, bool is_device_va);

int  perftest_gdr_eye_init_gpu_recv(void *recv_dptr, uint32_t size);
int  perftest_gdr_eye_dump_gpu_recv(void *recv_dptr, uint32_t size);

#ifdef __cplusplus
}
#endif

#endif /* PERFTEST_VALIDATION_H */
