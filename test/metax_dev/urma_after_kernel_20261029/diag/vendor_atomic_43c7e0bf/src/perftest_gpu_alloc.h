/*
 * SPDX-License-Identifier: MIT
 * Copyright (c) Huawei Technologies Co., Ltd. 2024-2026. All rights reserved.
 *
 * GPU buffer alloc for *_gpu perftest commands, selected by --gpu-mode:
 *   none | proxy | proxy_pure | peermem | direct(dma-buf) | metax, with memalign fallback.
 *
 * proxy      : host shadow + GPU; BW/LAT timed path includes staging
 *              (WRITE D2H before post, READ H2D after CQE, LAT both).
 * proxy_pure : same alloc/register as proxy; BW/LAT do NOT stage (NIC-only).
 */

#ifndef PERFTEST_GPU_ALLOC_H
#define PERFTEST_GPU_ALLOC_H

#include <stdbool.h>
#include <stddef.h>
#include <stdint.h>

#ifdef __cplusplus
extern "C" {
#endif

typedef enum perftest_gpu_mode {
    PERFTEST_GPU_MODE_NONE       = 0,
    PERFTEST_GPU_MODE_PROXY      = 1,
    PERFTEST_GPU_MODE_PEERMEM    = 2,
    PERFTEST_GPU_MODE_DIRECT     = 3,
    PERFTEST_GPU_MODE_PROXY_PURE = 4,
    PERFTEST_GPU_MODE_METAX      = 5, /* MetaX MACA mcMalloc + kernel gpu_vendor=metax */
} perftest_gpu_mode_t;

typedef enum perftest_gpu_alloc_strategy {
    PERFTEST_GPU_ALLOC_NONE       = 0,
    PERFTEST_GPU_ALLOC_GDR        = 1,
    PERFTEST_GPU_ALLOC_PEERMEM    = 2,
    PERFTEST_GPU_ALLOC_HOST_PROXY = 3,
    PERFTEST_GPU_ALLOC_MEMALIGN   = 4,
    PERFTEST_GPU_ALLOC_METAX      = 5,
} perftest_gpu_alloc_strategy_t;

typedef struct perftest_gpu_buffer {
    void                          *addr;           /* VA for urma_register_seg */
    size_t                         size;
    int                            dmabuf_fd;      /* >=0 only for GDR */
    uint64_t                       dmabuf_offset;
    void                          *gpu_ptr;        /* device ptr (peermem/GDR/proxy) */
    void                          *cpu_flag;       /* 1B D2H/H2D fallback scratch */
    void                          *bar_map_raw;    /* gdr_map() base, or NULL */
    void                          *bar_cpu_base;   /* CPU VA for gpu_ptr, or NULL */
    size_t                         bar_map_size;
    unsigned long                  bar_mh;
    perftest_gpu_alloc_strategy_t  strategy;
} perftest_gpu_buffer_t;

int  perftest_gpu_alloc_buffer(size_t size, size_t alignment,
                               perftest_gpu_buffer_t *out);
void perftest_gpu_free_buffer(perftest_gpu_buffer_t *buf);

void                perftest_gpu_set_mode(perftest_gpu_mode_t m);
void                perftest_gpu_set_device_id(int dev_id);
int                 perftest_gpu_get_device_id(void);
perftest_gpu_mode_t perftest_gpu_get_mode(void);
const char         *perftest_gpu_mode_name(perftest_gpu_mode_t m);
int                 perftest_gpu_mode_from_string(const char *s,
                                                  perftest_gpu_mode_t *out);

/* True only for --gpu-mode=proxy (not proxy_pure). */
bool perftest_gpu_proxy_stages(void);

int  perftest_gpu_stage_to_nic(perftest_gpu_buffer_t *buf, size_t len);
int  perftest_gpu_stage_from_nic(perftest_gpu_buffer_t *buf, size_t len);
int  perftest_gpu_stage_from_nic_range(perftest_gpu_buffer_t *buf,
                                       size_t off, size_t len);
int  perftest_gpu_stage_to_nic_range(perftest_gpu_buffer_t *buf,
                                     size_t off, size_t len);

bool  perftest_gpu_is_device_va(const perftest_gpu_buffer_t *buf);
bool  perftest_gpu_has_bar_map(const perftest_gpu_buffer_t *buf);
void *perftest_gpu_bar_from_gpu(const perftest_gpu_buffer_t *buf, void *gpu_va);
bool  perftest_gpu_needs_cpu_flag(const perftest_gpu_buffer_t *buf);
int   perftest_gpu_flag_load(perftest_gpu_buffer_t *buf, void *gpu_flag_va,
                             char *out);
int   perftest_gpu_flag_store(perftest_gpu_buffer_t *buf, void *gpu_flag_va,
                              char val);

const char *perftest_gpu_alloc_strategy_name(perftest_gpu_alloc_strategy_t s);
void perftest_gpu_alloc_set_force_fail_mask(unsigned mask);

#ifdef __cplusplus
}
#endif

#endif /* PERFTEST_GPU_ALLOC_H */
