/*
 * SPDX-License-Identifier: MIT
 * Copyright (c) Huawei Technologies Co., Ltd. 2024-2026. All rights reserved.
 *
 * Thin CUDA Driver API loader (dlopen libcuda.so.1; no build-time CUDA SDK).
 */

#ifndef PERFTEST_CUDA_LOADER_H
#define PERFTEST_CUDA_LOADER_H

#include <stddef.h>
#include <stdint.h>

#ifdef __cplusplus
extern "C" {
#endif

int perftest_cuda_set_current_ctx(void);

int  perftest_cuda_loader_init(void);
void perftest_cuda_loader_fini(void);
int  perftest_cuda_is_loaded(void);

int  perftest_cuda_ctx_create(int dev_id);
void perftest_cuda_ctx_destroy(void);

int  perftest_cuda_mem_alloc(size_t size, void **out_dptr);
int  perftest_cuda_mem_free(void *dptr);

int  perftest_cuda_mem_alloc_host(size_t size, void **out_hptr);
int  perftest_cuda_mem_free_host(void *hptr);

/* Required before nvidia_p2p_get_pages (SYNC_MEMOPS). */
int  perftest_cuda_set_sync_memops(void *dptr);

int  perftest_cuda_memcpy_d2h(void *host, void *dev, size_t n);
int  perftest_cuda_memcpy_h2d(void *dev, const void *host, size_t n);

int  perftest_cuda_get_dmabuf_fd(void *dptr, size_t size,
                                 int *out_fd, uint64_t *out_offset);

#ifdef __cplusplus
}
#endif

#endif /* PERFTEST_CUDA_LOADER_H */
