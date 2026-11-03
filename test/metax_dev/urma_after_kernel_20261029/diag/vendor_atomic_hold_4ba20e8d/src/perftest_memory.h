/*
 * SPDX-License-Identifier: MIT
 * Copyright (c) Huawei Technologies Co., Ltd. 2024-2026. All rights reserved.
 *
 * Legacy pluggable memory-backend stubs (compile-time only).
 * Active *_gpu path uses perftest_gpu_alloc.h + --gpu-mode.
 */

#ifndef PERFTEST_MEMORY_H
#define PERFTEST_MEMORY_H

#include <stdbool.h>
#include <stddef.h>
#include <stdint.h>

struct perftest_config;
typedef struct perftest_config perftest_config_t;

typedef enum perftest_mem_type {
    PERFTEST_MEM_HOST   = 0,
    PERFTEST_MEM_CUDA   = 1,
    PERFTEST_MEM_ROCM   = 2,
    PERFTEST_MEM_DMABUF = 3,
    PERFTEST_MEM_TYPE_MAX
} perftest_mem_type_t;

typedef enum perftest_gdr_mode {
    PERFTEST_GDR_AUTO       = 0,
    PERFTEST_GDR_DIRECT     = 1,
    PERFTEST_GDR_HOST_PROXY = 2,
    PERFTEST_GDR_OFF        = 3,
    PERFTEST_GDR_MODE_MAX
} perftest_gdr_mode_t;

typedef struct perftest_memory_ops {
    int  (*init)(struct perftest_memory_ops *self, perftest_config_t *cfg);
    void (*destroy)(struct perftest_memory_ops *self);

    int  (*alloc_buffer)(struct perftest_memory_ops *self,
                         uint64_t size, uint64_t alignment,
                         void **out_addr,
                         int *out_dmabuf_fd,
                         uint64_t *out_dmabuf_offset);
    void (*free_buffer)(struct perftest_memory_ops *self,
                        void *addr, int dmabuf_fd, uint64_t size);

    void *(*copy_host_to_buffer)(struct perftest_memory_ops *self,
                                 void *dst, const void *src, size_t n);
    void *(*copy_buffer_to_host)(struct perftest_memory_ops *self,
                                 void *dst, const void *src, size_t n);
    void *(*copy_buffer_to_buffer)(struct perftest_memory_ops *self,
                                   void *dst, const void *src, size_t n);

    void   (*write_byte)(struct perftest_memory_ops *self, void *addr,
                         uint64_t off, uint8_t v);
    uint8_t (*read_byte)(struct perftest_memory_ops *self, const void *addr,
                         uint64_t off);

    int  (*negotiate_gdr_mode)(struct perftest_memory_ops *self,
                               void *urma_dev_opaque);
    int  (*stage_to_nic_buffer)(struct perftest_memory_ops *self,
                                void *shadow_addr, void *gpu_addr, size_t len);
    int  (*stage_from_nic_buffer)(struct perftest_memory_ops *self,
                                  void *gpu_addr, void *shadow_addr, size_t len);

    perftest_mem_type_t  type;
    perftest_gdr_mode_t  gdr_mode;
    void                *priv;
} perftest_memory_ops_t;

perftest_memory_ops_t *perftest_host_mem_create(perftest_config_t *cfg);
perftest_memory_ops_t *perftest_cuda_mem_create(perftest_config_t *cfg);
perftest_memory_ops_t *perftest_rocm_mem_create(perftest_config_t *cfg);
perftest_memory_ops_t *perftest_dmabuf_mem_create(perftest_config_t *cfg);

perftest_memory_ops_t *perftest_gpu_proxy_create(perftest_config_t *cfg);
void                  *perftest_gpu_proxy_get_gpu_ptr(perftest_memory_ops_t *ops);
size_t                 perftest_gpu_proxy_get_size(perftest_memory_ops_t *ops);

perftest_memory_ops_t *perftest_memory_create(perftest_config_t *cfg);
void                   perftest_memory_destroy(perftest_memory_ops_t *ops);

#endif /* PERFTEST_MEMORY_H */
