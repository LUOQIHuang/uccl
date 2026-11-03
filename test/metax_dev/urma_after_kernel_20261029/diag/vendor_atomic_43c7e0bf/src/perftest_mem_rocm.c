/*
 * SPDX-License-Identifier: MIT
 * Copyright (c) Huawei Technologies Co., Ltd. 2024-2026. All rights reserved.
 *
 * Legacy stub / test helper: perftest_mem_rocm.c
 */
#include <stdio.h>
#include <stdlib.h>
#include <string.h>

#include "perftest_memory.h"

typedef struct rocm_priv {
    int   gpu_id;          /* TODO: hipDevice_t */
    void *hipctx_opaque;   /* TODO: hipCtx_t */
    int   use_dmabuf;
} rocm_priv_t;

static int rocm_init(perftest_memory_ops_t *self, perftest_config_t *cfg)
{
    (void)self; (void)cfg;
    /*
     * TODO: hipInit(0); hipSetDevice(priv->gpu_id);
     *       hipDeviceGetAttribute(...) 检查 hipDeviceAttributeDmaBufSupport.
     */
    fprintf(stderr, "[perftest-mem][rocm] init: stub ok (no real hipInit)\n");
    return 0;
}

static void rocm_destroy(perftest_memory_ops_t *self)
{
    (void)self;
    /* TODO: hipDeviceReset(); free(priv); */
    fprintf(stderr, "[perftest-mem][rocm] destroy: stub ok\n");
}

static int rocm_alloc_buffer(perftest_memory_ops_t *self,
                             uint64_t size, uint64_t alignment,
                             void **out_addr,
                             int *out_dmabuf_fd,
                             uint64_t *out_dmabuf_offset)
{
    (void)self; (void)size; (void)alignment;
    
    if (out_addr)          *out_addr = NULL;
    if (out_dmabuf_fd)     *out_dmabuf_fd = -1;
    if (out_dmabuf_offset) *out_dmabuf_offset = 0;
    fprintf(stderr, "[perftest-mem][rocm] alloc_buffer: stub ok (size=%lu)\n",
            (unsigned long)size);
    return 0;
}

static void rocm_free_buffer(perftest_memory_ops_t *self,
                             void *addr, int dmabuf_fd, uint64_t size)
{
    (void)self; (void)addr; (void)dmabuf_fd; (void)size;
    /* TODO: if (fd>=0) close(fd); hipFree(addr); */
    fprintf(stderr, "[perftest-mem][rocm] free_buffer: stub ok\n");
}

static void *rocm_copy_h2b(perftest_memory_ops_t *self,
                           void *dst, const void *src, size_t n)
{
    (void)self; (void)src;
    /* TODO: hipMemcpy(dst, src, n, hipMemcpyHostToDevice); */
    fprintf(stderr, "[perftest-mem][rocm] copy_h2b stub: %zu bytes\n", n);
    return dst;
}

static void *rocm_copy_b2h(perftest_memory_ops_t *self,
                           void *dst, const void *src, size_t n)
{
    (void)self; (void)src;
    /* TODO: hipMemcpy(dst, src, n, hipMemcpyDeviceToHost); */
    fprintf(stderr, "[perftest-mem][rocm] copy_b2h stub: %zu bytes\n", n);
    return dst;
}

static void *rocm_copy_b2b(perftest_memory_ops_t *self,
                           void *dst, const void *src, size_t n)
{
    (void)self; (void)src;
    /* TODO: hipMemcpy(dst, src, n, hipMemcpyDeviceToDevice); */
    fprintf(stderr, "[perftest-mem][rocm] copy_b2b stub: %zu bytes\n", n);
    return dst;
}

static void rocm_write_byte(perftest_memory_ops_t *self, void *addr,
                            uint64_t off, uint8_t v)
{
    (void)self; (void)addr; (void)off; (void)v;
    /* 同 cuda 后端: latency sentinel 必须落在 host pinned shadow 上. */
    fprintf(stderr, "[perftest-mem][rocm] write_byte stub off=%lu v=%u\n",
            (unsigned long)off, (unsigned)v);
}

static uint8_t rocm_read_byte(perftest_memory_ops_t *self, const void *addr,
                              uint64_t off)
{
    (void)self; (void)addr; (void)off;
    fprintf(stderr, "[perftest-mem][rocm] read_byte stub off=%lu -> 0\n",
            (unsigned long)off);
    return 0;
}

static int rocm_negotiate(perftest_memory_ops_t *self, void *urma_dev_opaque)
{
    /* 同 cuda_negotiate 思路, 只是 GPU 一侧用 hipDeviceGetAttribute 探测. */
    (void)urma_dev_opaque;
    self->gdr_mode = PERFTEST_GDR_HOST_PROXY;
    fprintf(stderr,
            "[perftest-mem][rocm] negotiate: driver dma-buf NOT supported -> host-proxy\n");
    return 0;
}

static int rocm_stage_to_nic(perftest_memory_ops_t *self, void *shadow_addr,
                             void *gpu_addr, size_t len)
{
    (void)shadow_addr; (void)gpu_addr;
    if (self->gdr_mode != PERFTEST_GDR_HOST_PROXY) return 0;
    fprintf(stderr,
            "[perftest-mem][rocm] stage_to_nic stub: would hipMemcpyDtoH(shadow,gpu,%zu)\n",
            len);
    return 0;
}

static int rocm_stage_from_nic(perftest_memory_ops_t *self, void *gpu_addr,
                               void *shadow_addr, size_t len)
{
    (void)gpu_addr; (void)shadow_addr;
    if (self->gdr_mode != PERFTEST_GDR_HOST_PROXY) return 0;
    fprintf(stderr,
            "[perftest-mem][rocm] stage_from_nic stub: would hipMemcpyHtoD(gpu,shadow,%zu)\n",
            len);
    return 0;
}

perftest_memory_ops_t *perftest_rocm_mem_create(perftest_config_t *cfg)
{
    (void)cfg;
    perftest_memory_ops_t *ops = (perftest_memory_ops_t *)calloc(1, sizeof(*ops));
    if (!ops) {
        fprintf(stderr, "[perftest-mem][rocm] create: oom\n");
        return NULL;
    }
    rocm_priv_t *priv = (rocm_priv_t *)calloc(1, sizeof(*priv));
    if (!priv) {
        free(ops);
        return NULL;
    }
    ops->type                  = PERFTEST_MEM_ROCM;
    ops->gdr_mode              = PERFTEST_GDR_AUTO;
    ops->priv                  = priv;
    ops->init                  = rocm_init;
    ops->destroy               = rocm_destroy;
    ops->alloc_buffer          = rocm_alloc_buffer;
    ops->free_buffer           = rocm_free_buffer;
    ops->copy_host_to_buffer   = rocm_copy_h2b;
    ops->copy_buffer_to_host   = rocm_copy_b2h;
    ops->copy_buffer_to_buffer = rocm_copy_b2b;
    ops->write_byte            = rocm_write_byte;
    ops->read_byte             = rocm_read_byte;
    ops->negotiate_gdr_mode    = rocm_negotiate;
    ops->stage_to_nic_buffer   = rocm_stage_to_nic;
    ops->stage_from_nic_buffer = rocm_stage_from_nic;
    fprintf(stderr, "[perftest-mem][rocm] create: stub ok\n");
    return ops;
}
