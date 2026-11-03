/*
 * SPDX-License-Identifier: MIT
 * Copyright (c) Huawei Technologies Co., Ltd. 2024-2026. All rights reserved.
 *
 * Legacy stub / test helper: perftest_mem_dmabuf.c
 */
#include <stdio.h>
#include <stdlib.h>
#include <string.h>

#include "perftest_memory.h"

typedef struct dmabuf_priv {
    int      ext_fd;       /* TODO: 由 perftest_config_t 透传 */
    uint64_t ext_offset;
    void    *mmap_addr;
    uint64_t mmap_size;
} dmabuf_priv_t;

static int dmabuf_init(perftest_memory_ops_t *self, perftest_config_t *cfg)
{
    (void)self; (void)cfg;
    /*
     * TODO 真实现:
     *   priv->ext_fd     = cfg->dmabuf_fd;
     *   priv->ext_offset = cfg->dmabuf_offset;
     *   if (priv->ext_fd < 0) return -1;
     */
    fprintf(stderr, "[perftest-mem][dmabuf] init: stub ok\n");
    return 0;
}

static void dmabuf_destroy(perftest_memory_ops_t *self)
{
    (void)self;
    /* TODO: free(priv) */
    fprintf(stderr, "[perftest-mem][dmabuf] destroy: stub ok\n");
}

static int dmabuf_alloc_buffer(perftest_memory_ops_t *self,
                               uint64_t size, uint64_t alignment,
                               void **out_addr,
                               int *out_dmabuf_fd,
                               uint64_t *out_dmabuf_offset)
{
    (void)self; (void)size; (void)alignment;
    
    if (out_addr)          *out_addr = NULL;
    if (out_dmabuf_fd)     *out_dmabuf_fd = -1;
    if (out_dmabuf_offset) *out_dmabuf_offset = 0;
    fprintf(stderr, "[perftest-mem][dmabuf] alloc_buffer: stub ok\n");
    return 0;
}

static void dmabuf_free_buffer(perftest_memory_ops_t *self,
                               void *addr, int dmabuf_fd, uint64_t size)
{
    (void)self; (void)addr; (void)dmabuf_fd; (void)size;
    /* TODO: if (addr) munmap(addr, size); 不主动 close(fd), 由 caller 决定 */
    fprintf(stderr, "[perftest-mem][dmabuf] free_buffer: stub ok\n");
}

static void *dmabuf_copy_h2b(perftest_memory_ops_t *self,
                             void *dst, const void *src, size_t n)
{
    (void)self; (void)src;
    /* 仅当 dma-buf exporter 支持 mmap 时才能 memcpy, 否则放弃数据校验. */
    fprintf(stderr, "[perftest-mem][dmabuf] copy_h2b stub: %zu bytes\n", n);
    return dst;
}

static void *dmabuf_copy_b2h(perftest_memory_ops_t *self,
                             void *dst, const void *src, size_t n)
{
    (void)self; (void)src;
    fprintf(stderr, "[perftest-mem][dmabuf] copy_b2h stub: %zu bytes\n", n);
    return dst;
}

static void *dmabuf_copy_b2b(perftest_memory_ops_t *self,
                             void *dst, const void *src, size_t n)
{
    (void)self; (void)src;
    fprintf(stderr, "[perftest-mem][dmabuf] copy_b2b stub: %zu bytes\n", n);
    return dst;
}

static void dmabuf_write_byte(perftest_memory_ops_t *self, void *addr,
                              uint64_t off, uint8_t v)
{
    (void)self; (void)addr; (void)off; (void)v;
    fprintf(stderr, "[perftest-mem][dmabuf] write_byte stub off=%lu v=%u\n",
            (unsigned long)off, (unsigned)v);
}

static uint8_t dmabuf_read_byte(perftest_memory_ops_t *self, const void *addr,
                                uint64_t off)
{
    (void)self; (void)addr; (void)off;
    fprintf(stderr, "[perftest-mem][dmabuf] read_byte stub off=%lu -> 0\n",
            (unsigned long)off);
    return 0;
}

static int dmabuf_negotiate(perftest_memory_ops_t *self, void *urma_dev_opaque)
{
    
    (void)urma_dev_opaque;
    self->gdr_mode = PERFTEST_GDR_HOST_PROXY;
    fprintf(stderr,
            "[perftest-mem][dmabuf] negotiate: NIC dma-buf NOT supported -> "
            "host-proxy (will mmap fd as host shadow if exporter allows)\n");
    return 0;
}

static int dmabuf_stage_to_nic(perftest_memory_ops_t *self, void *shadow_addr,
                               void *gpu_addr, size_t len)
{
    (void)self; (void)shadow_addr; (void)gpu_addr;
    /*
     * 通用 dma-buf 后端没法做"GPU->host"的拷贝 (没有运行时), 只能依赖外部
     * 进程在写完 fd 之后调用 perftest. 这里 stage_to_nic 真正可做的事是
     * dma_buf_sync(DMA_BUF_SYNC_START | DMA_BUF_SYNC_WRITE) 用户态形式
     * (DMA_BUF_IOCTL_SYNC), 让缓存一致性生效.
     */
    fprintf(stderr,
            "[perftest-mem][dmabuf] stage_to_nic stub: would DMA_BUF_IOCTL_SYNC START %zu\n",
            len);
    return 0;
}

static int dmabuf_stage_from_nic(perftest_memory_ops_t *self, void *gpu_addr,
                                 void *shadow_addr, size_t len)
{
    (void)self; (void)gpu_addr; (void)shadow_addr;
    fprintf(stderr,
            "[perftest-mem][dmabuf] stage_from_nic stub: would DMA_BUF_IOCTL_SYNC END %zu\n",
            len);
    return 0;
}

perftest_memory_ops_t *perftest_dmabuf_mem_create(perftest_config_t *cfg)
{
    (void)cfg;
    perftest_memory_ops_t *ops = (perftest_memory_ops_t *)calloc(1, sizeof(*ops));
    if (!ops) {
        fprintf(stderr, "[perftest-mem][dmabuf] create: oom\n");
        return NULL;
    }
    dmabuf_priv_t *priv = (dmabuf_priv_t *)calloc(1, sizeof(*priv));
    if (!priv) {
        free(ops);
        return NULL;
    }
    priv->ext_fd = -1;
    ops->type                  = PERFTEST_MEM_DMABUF;
    ops->gdr_mode              = PERFTEST_GDR_AUTO;
    ops->priv                  = priv;
    ops->init                  = dmabuf_init;
    ops->destroy               = dmabuf_destroy;
    ops->alloc_buffer          = dmabuf_alloc_buffer;
    ops->free_buffer           = dmabuf_free_buffer;
    ops->copy_host_to_buffer   = dmabuf_copy_h2b;
    ops->copy_buffer_to_host   = dmabuf_copy_b2h;
    ops->copy_buffer_to_buffer = dmabuf_copy_b2b;
    ops->write_byte            = dmabuf_write_byte;
    ops->read_byte             = dmabuf_read_byte;
    ops->negotiate_gdr_mode    = dmabuf_negotiate;
    ops->stage_to_nic_buffer   = dmabuf_stage_to_nic;
    ops->stage_from_nic_buffer = dmabuf_stage_from_nic;
    fprintf(stderr, "[perftest-mem][dmabuf] create: stub ok\n");
    return ops;
}

perftest_memory_ops_t *perftest_memory_create(perftest_config_t *cfg)
{
    const char *sel = getenv("PERFTEST_MEM");
    if (sel == NULL) {
        sel = "host";
    }
    fprintf(stderr, "[perftest-mem] %s: backend=%s\n", __func__, sel);

    if (strcmp(sel, "gpu_proxy") == 0) {
        return perftest_gpu_proxy_create(cfg);
    }
    if (strcmp(sel, "cuda") == 0) {
        return perftest_cuda_mem_create(cfg);
    }
    if (strcmp(sel, "rocm") == 0) {
        return perftest_rocm_mem_create(cfg);
    }
    if (strcmp(sel, "dmabuf") == 0) {
        return perftest_dmabuf_mem_create(cfg);
    }
    return perftest_host_mem_create(cfg);
}

void perftest_memory_destroy(perftest_memory_ops_t *ops)
{
    if (!ops) return;
    if (ops->destroy) ops->destroy(ops);
    if (ops->priv)    free(ops->priv);
    free(ops);
}
