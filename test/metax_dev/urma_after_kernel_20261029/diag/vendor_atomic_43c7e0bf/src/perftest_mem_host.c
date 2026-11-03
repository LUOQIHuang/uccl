/*
 * SPDX-License-Identifier: MIT
 * Copyright (c) Huawei Technologies Co., Ltd. 2024-2026. All rights reserved.
 *
 * Legacy stub / test helper: perftest_mem_host.c
 */
#include <stdio.h>
#include <stdlib.h>
#include <string.h>

#include "perftest_memory.h"
/* 注意: 这里不要 include perftest_parameters.h 以避免循环依赖,
 * perftest_config_t 通过前向声明从 perftest_memory.h 拿到. */

static int host_init(perftest_memory_ops_t *self, perftest_config_t *cfg)
{
    (void)self; (void)cfg;
    /*
     * TODO: 真正实现里这里基本是空的, 因为主机内存不需要 cuInit 之类的.
     *       只需要把 cfg->use_huge_page / cfg->huge_page / cfg->page_size
     *       缓存到 self->priv 里供 alloc_buffer 用.
     */
    fprintf(stderr, "[perftest-mem][host] init: stub ok\n");
    return 0;
}

static void host_destroy(perftest_memory_ops_t *self)
{
    (void)self;
    /* TODO: free(self->priv) 与 close 任何 fd. */
    fprintf(stderr, "[perftest-mem][host] destroy: stub ok\n");
}

static int host_alloc_buffer(perftest_memory_ops_t *self,
                             uint64_t size, uint64_t alignment,
                             void **out_addr,
                             int *out_dmabuf_fd,
                             uint64_t *out_dmabuf_offset)
{
    (void)self; (void)size; (void)alignment;
    
    if (out_addr)          *out_addr = NULL;
    if (out_dmabuf_fd)     *out_dmabuf_fd = -1;
    if (out_dmabuf_offset) *out_dmabuf_offset = 0;
    fprintf(stderr, "[perftest-mem][host] alloc_buffer: stub ok (no real alloc)\n");
    return 0;
}

static void host_free_buffer(perftest_memory_ops_t *self,
                             void *addr, int dmabuf_fd, uint64_t size)
{
    (void)self; (void)addr; (void)dmabuf_fd; (void)size;
    /* TODO: 与 alloc 配对, free / ub_hugefree, 关 fd. */
    fprintf(stderr, "[perftest-mem][host] free_buffer: stub ok\n");
}

static void *host_copy_h2b(perftest_memory_ops_t *self,
                           void *dst, const void *src, size_t n)
{
    (void)self;
    /* host 模式下 dst/src 都是普通 va, 直接 memcpy. */
    fprintf(stderr, "[perftest-mem][host] copy_h2b stub: %zu bytes\n", n);
    return dst ? memcpy(dst, src, 0) : NULL; /* 真实现去掉 0, 这里仅占位 */
}

static void *host_copy_b2h(perftest_memory_ops_t *self,
                           void *dst, const void *src, size_t n)
{
    (void)self;
    fprintf(stderr, "[perftest-mem][host] copy_b2h stub: %zu bytes\n", n);
    return dst ? memcpy(dst, src, 0) : NULL;
}

static void *host_copy_b2b(perftest_memory_ops_t *self,
                           void *dst, const void *src, size_t n)
{
    (void)self;
    fprintf(stderr, "[perftest-mem][host] copy_b2b stub: %zu bytes\n", n);
    return dst ? memcpy(dst, src, 0) : NULL;
}

static void host_write_byte(perftest_memory_ops_t *self, void *addr,
                            uint64_t off, uint8_t v)
{
    (void)self; (void)addr; (void)off; (void)v;
    /*
     * 真实现: ((volatile uint8_t *)addr)[off] = v;
     * latency 主循环依赖这个 sentinel, 现在仅打印.
     */
    fprintf(stderr, "[perftest-mem][host] write_byte stub off=%lu v=%u\n",
            (unsigned long)off, (unsigned)v);
}

static uint8_t host_read_byte(perftest_memory_ops_t *self, const void *addr,
                              uint64_t off)
{
    (void)self; (void)addr; (void)off;
    /* 真实现: return ((const volatile uint8_t *)addr)[off]; */
    fprintf(stderr, "[perftest-mem][host] read_byte stub off=%lu -> 0\n",
            (unsigned long)off);
    return 0;
}

static int host_negotiate(perftest_memory_ops_t *self, void *urma_dev_opaque)
{
    (void)urma_dev_opaque;
    /*
     * host 后端没有"GDR"可言, 永远是 OFF. 这里 print 一行让人在日志里
     * 一眼看出当前模式.
     */
    self->gdr_mode = PERFTEST_GDR_OFF;
    fprintf(stderr, "[perftest-mem][host] negotiate: gdr_mode = OFF (host only)\n");
    return 0;
}

static int host_stage_to_nic(perftest_memory_ops_t *self, void *shadow_addr,
                             void *gpu_addr, size_t len)
{
    (void)self; (void)shadow_addr; (void)gpu_addr; (void)len;
    /* host 模式下 shadow == gpu_addr == 同一个 host buffer, 这个钩子是 noop. */
    return 0;
}

static int host_stage_from_nic(perftest_memory_ops_t *self, void *gpu_addr,
                               void *shadow_addr, size_t len)
{
    (void)self; (void)gpu_addr; (void)shadow_addr; (void)len;
    return 0;
}

perftest_memory_ops_t *perftest_host_mem_create(perftest_config_t *cfg)
{
    (void)cfg;
    /*
     * 这就是后端的对外入口. 主程序只调一次:
     *     ops = perftest_host_mem_create(cfg);
     *     ops->init(ops, cfg);
     */
    perftest_memory_ops_t *ops = (perftest_memory_ops_t *)calloc(1, sizeof(*ops));
    if (!ops) {
        fprintf(stderr, "[perftest-mem][host] create: oom\n");
        return NULL;
    }
    ops->type                  = PERFTEST_MEM_HOST;
    ops->gdr_mode              = PERFTEST_GDR_OFF;
    ops->init                  = host_init;
    ops->destroy               = host_destroy;
    ops->alloc_buffer          = host_alloc_buffer;
    ops->free_buffer           = host_free_buffer;
    ops->copy_host_to_buffer   = host_copy_h2b;
    ops->copy_buffer_to_host   = host_copy_b2h;
    ops->copy_buffer_to_buffer = host_copy_b2b;
    ops->write_byte            = host_write_byte;
    ops->read_byte             = host_read_byte;
    ops->negotiate_gdr_mode    = host_negotiate;
    ops->stage_to_nic_buffer   = host_stage_to_nic;
    ops->stage_from_nic_buffer = host_stage_from_nic;
    fprintf(stderr, "[perftest-mem][host] create: stub ok\n");
    return ops;
}
