/*
 * SPDX-License-Identifier: MIT
 * Copyright (c) Huawei Technologies Co., Ltd. 2024-2026. All rights reserved.
 *
 * Thin MACA runtime loader (dlopen libmcruntime; no mc_runtime.h at build).
 * Used by --gpu-mode=metax so urma_perftest can mcMalloc a MetaX GPU VA
 * that kernel udma.ko (gpu_vendor=metax) pins via metax_p2p_acquire_mem.
 */

#ifndef PERFTEST_METAX_LOADER_H
#define PERFTEST_METAX_LOADER_H

#include <stddef.h>

#ifdef __cplusplus
extern "C" {
#endif

int  perftest_metax_loader_init(void);
void perftest_metax_loader_fini(void);
int  perftest_metax_is_loaded(void);

int  perftest_metax_set_device(int dev_id);
int  perftest_metax_mem_alloc(size_t size, void **out_dptr);
int  perftest_metax_mem_free(void *dptr);
int  perftest_metax_memcpy_d2h(void *host, void *dev, size_t n);
int  perftest_metax_memcpy_h2d(void *dev, const void *host, size_t n);

#ifdef __cplusplus
}
#endif

#endif /* PERFTEST_METAX_LOADER_H */
