/*
 * SPDX-License-Identifier: MIT
 * Copyright (c) Huawei Technologies Co., Ltd. 2024-2026. All rights reserved.
 *
 * Legacy stub / test helper: perftest_mem_gpu_proxy.c
 */
#include <errno.h>
#include <stdio.h>
#include <stdlib.h>
#include <string.h>
#include <stdint.h>

#ifndef _GNU_SOURCE
#define _GNU_SOURCE
#endif
#include <malloc.h>   /* memalign */

#include "perftest_memory.h"

typedef struct gpu_proxy_priv {
    void   *gpu_ptr;
    void   *shadow;
    size_t  size;
    int     gpu_id;
} gpu_proxy_priv_t;

#define GPU_PROXY_DEFAULT_ALIGN 4096   /* 模拟"GPU page", 64KB 起步也可以 */

#define PROXY_LOG(fmt, ...) \
    fprintf(stderr, "[gpu-proxy] %s: " fmt "\n", __func__, ##__VA_ARGS__)

static int gpu_proxy_init(perftest_memory_ops_t *self, perftest_config_t *cfg)
{
    gpu_proxy_priv_t *priv = (gpu_proxy_priv_t *)self->priv;

    (void)cfg;
    if (priv == NULL) {
        PROXY_LOG("priv is NULL");
        return -1;
    }
    priv->gpu_ptr = NULL;
    priv->shadow  = NULL;
    priv->size    = 0;
    /*
     * 真实环境里 gpu_id 应当来自 cfg->gpu_dev_id (CLI --use_cuda=ID).
     * 当前 cfg 还没加这个字段, 占位 0 即可.
     */
    priv->gpu_id  = 0;

    PROXY_LOG("ok (simulated GPU id=%d, no CUDA/HIP linked)", priv->gpu_id);
    return 0;
}

static void gpu_proxy_destroy(perftest_memory_ops_t *self)
{
    /*
     * destroy 与 init 严格配对, 真实 CUDA 里要 cuCtxDestroy.
     * 这里只确保没有残留的 buffer; 资源清理在 free_buffer 完成.
     */
    gpu_proxy_priv_t *priv = (gpu_proxy_priv_t *)self->priv;
    if (priv && (priv->gpu_ptr || priv->shadow)) {
        PROXY_LOG("WARN: buffer not freed before destroy (gpu=%p shadow=%p)",
                  priv->gpu_ptr, priv->shadow);
    }
    PROXY_LOG("ok");
}

static int gpu_proxy_negotiate(perftest_memory_ops_t *self, void *urma_dev_opaque)
{
    (void)urma_dev_opaque;
    self->gdr_mode = PERFTEST_GDR_HOST_PROXY;
    PROXY_LOG("forced gdr_mode=HOST_PROXY (GDR path bypassed, kernel not involved)");
    return 0;
}

static int gpu_proxy_alloc_buffer(perftest_memory_ops_t *self,
                                  uint64_t size, uint64_t alignment,
                                  void **out_addr,
                                  int *out_dmabuf_fd,
                                  uint64_t *out_dmabuf_offset)
{
    gpu_proxy_priv_t *priv = (gpu_proxy_priv_t *)self->priv;
    size_t align;

    if (priv == NULL || out_addr == NULL || out_dmabuf_fd == NULL ||
        out_dmabuf_offset == NULL) {
        PROXY_LOG("invalid arg");
        errno = EINVAL;
        return -1;
    }

    align = alignment ? (size_t)alignment : GPU_PROXY_DEFAULT_ALIGN;

    /* 1) 分配"GPU"缓冲区 (本骨架: memalign 模拟). */
    priv->gpu_ptr = memalign(align, (size_t)size);
    if (priv->gpu_ptr == NULL) {
        PROXY_LOG("ENOMEM allocating gpu_ptr size=%lu align=%zu",
                  (unsigned long)size, align);
        errno = ENOMEM;
        return -1;
    }
    /*
     * 给"GPU 数据"打个特征值, 方便 demo 看到数据真的搬运过.
     * 真实部署不需要这步, 用户应用自己写 GPU 数据.
     */
    memset(priv->gpu_ptr, 0xA5, (size_t)size);

    /* 2) 分配 host pinned shadow. */
    priv->shadow = memalign(align, (size_t)size);
    if (priv->shadow == NULL) {
        free(priv->gpu_ptr);
        priv->gpu_ptr = NULL;
        PROXY_LOG("ENOMEM allocating shadow size=%lu", (unsigned long)size);
        errno = ENOMEM;
        return -1;
    }
    memset(priv->shadow, 0, (size_t)size);

    priv->size         = (size_t)size;
    *out_addr          = priv->shadow;   /* URMA 注册这块 host buffer */
    *out_dmabuf_fd     = -1;             /* 旁路 GDR 路径 */
    *out_dmabuf_offset = 0;

    PROXY_LOG("ok: gpu_ptr=%p shadow=%p size=%lu align=%zu dmabuf_fd=-1",
              priv->gpu_ptr, priv->shadow, (unsigned long)size, align);
    return 0;
}

static void gpu_proxy_free_buffer(perftest_memory_ops_t *self,
                                  void *addr, int dmabuf_fd, uint64_t size)
{
    gpu_proxy_priv_t *priv = (gpu_proxy_priv_t *)self->priv;

    (void)dmabuf_fd;
    /*
     * 上层传进来的 addr 就是当初 alloc_buffer 返回的 shadow. 我们也顺手把
     * 对应的 gpu_ptr 一起释放.
     */
    if (priv == NULL) {
        PROXY_LOG("priv NULL, leaking addr=%p size=%lu", addr, (unsigned long)size);
        return;
    }
    if (addr != priv->shadow) {
        PROXY_LOG("WARN: addr=%p != tracked shadow=%p (continuing)",
                  addr, priv->shadow);
    }

    PROXY_LOG("releasing gpu_ptr=%p shadow=%p size=%lu",
              priv->gpu_ptr, priv->shadow, (unsigned long)priv->size);

    free(priv->gpu_ptr); priv->gpu_ptr = NULL;
    free(priv->shadow);  priv->shadow  = NULL;
    priv->size = 0;
}

static void *gpu_proxy_copy_h2b(perftest_memory_ops_t *self,
                                void *dst, const void *src, size_t n)
{
    (void)self;
    PROXY_LOG("memcpy %zu bytes dst=%p src=%p", n, dst, src);
    return memcpy(dst, src, n);
}

static void *gpu_proxy_copy_b2h(perftest_memory_ops_t *self,
                                void *dst, const void *src, size_t n)
{
    (void)self;
    PROXY_LOG("memcpy %zu bytes dst=%p src=%p", n, dst, src);
    return memcpy(dst, src, n);
}

static void *gpu_proxy_copy_b2b(perftest_memory_ops_t *self,
                                void *dst, const void *src, size_t n)
{
    (void)self;
    PROXY_LOG("memcpy %zu bytes dst=%p src=%p", n, dst, src);
    return memcpy(dst, src, n);
}

static void gpu_proxy_write_byte(perftest_memory_ops_t *self, void *addr,
                                 uint64_t off, uint8_t v)
{
    (void)self;
    ((volatile uint8_t *)addr)[off] = v;
}

static uint8_t gpu_proxy_read_byte(perftest_memory_ops_t *self, const void *addr,
                                   uint64_t off)
{
    (void)self;
    return ((const volatile uint8_t *)addr)[off];
}

static int gpu_proxy_stage_to_nic(perftest_memory_ops_t *self,
                                  void *shadow_addr, void *gpu_addr, size_t len)
{
    gpu_proxy_priv_t *priv = (gpu_proxy_priv_t *)self->priv;

    if (self->gdr_mode != PERFTEST_GDR_HOST_PROXY) {
        /* DIRECT 模式下 NIC 直接读 GPU, 这条 hook 应为 noop. */
        return 0;
    }
    if (priv == NULL || shadow_addr == NULL || gpu_addr == NULL || len == 0) {
        PROXY_LOG("invalid arg shadow=%p gpu=%p len=%zu",
                  shadow_addr, gpu_addr, len);
        return -1;
    }
    if (len > priv->size) {
        PROXY_LOG("len=%zu exceeds allocated size=%zu", len, priv->size);
        return -1;
    }
    /*
     * Note: 真 CUDA 在这里要做 cuStreamSynchronize 才能确保前序 kernel 写完.
     * 主机模拟没有 stream, memcpy 立即同步.
     */
    memcpy(shadow_addr, gpu_addr, len);
    PROXY_LOG("staged %zu bytes  gpu(%p) -> shadow(%p) [simulating cuMemcpyDtoH]",
              len, gpu_addr, shadow_addr);
    return 0;
}

static int gpu_proxy_stage_from_nic(perftest_memory_ops_t *self,
                                    void *gpu_addr, void *shadow_addr, size_t len)
{
    gpu_proxy_priv_t *priv = (gpu_proxy_priv_t *)self->priv;

    if (self->gdr_mode != PERFTEST_GDR_HOST_PROXY) {
        return 0;
    }
    if (priv == NULL || shadow_addr == NULL || gpu_addr == NULL || len == 0) {
        PROXY_LOG("invalid arg gpu=%p shadow=%p len=%zu",
                  gpu_addr, shadow_addr, len);
        return -1;
    }
    if (len > priv->size) {
        PROXY_LOG("len=%zu exceeds allocated size=%zu", len, priv->size);
        return -1;
    }
    memcpy(gpu_addr, shadow_addr, len);
    PROXY_LOG("staged %zu bytes  shadow(%p) -> gpu(%p) [simulating cuMemcpyHtoD]",
              len, shadow_addr, gpu_addr);
    return 0;
}

void *perftest_gpu_proxy_get_gpu_ptr(perftest_memory_ops_t *self)
{
    gpu_proxy_priv_t *priv;
    if (self == NULL) {
        return NULL;
    }
    priv = (gpu_proxy_priv_t *)self->priv;
    return priv ? priv->gpu_ptr : NULL;
}

size_t perftest_gpu_proxy_get_size(perftest_memory_ops_t *self)
{
    gpu_proxy_priv_t *priv;
    if (self == NULL) {
        return 0;
    }
    priv = (gpu_proxy_priv_t *)self->priv;
    return priv ? priv->size : 0;
}

perftest_memory_ops_t *perftest_gpu_proxy_create(perftest_config_t *cfg)
{
    perftest_memory_ops_t *ops;
    gpu_proxy_priv_t *priv;

    (void)cfg;
    ops = (perftest_memory_ops_t *)calloc(1, sizeof(*ops));
    if (ops == NULL) {
        PROXY_LOG("ENOMEM allocating ops");
        return NULL;
    }
    priv = (gpu_proxy_priv_t *)calloc(1, sizeof(*priv));
    if (priv == NULL) {
        PROXY_LOG("ENOMEM allocating priv");
        free(ops);
        return NULL;
    }

    ops->type                  = PERFTEST_MEM_CUDA; /* 复用 CUDA 类型 */
    ops->gdr_mode              = PERFTEST_GDR_HOST_PROXY;
    ops->priv                  = priv;
    ops->init                  = gpu_proxy_init;
    ops->destroy               = gpu_proxy_destroy;
    ops->alloc_buffer          = gpu_proxy_alloc_buffer;
    ops->free_buffer           = gpu_proxy_free_buffer;
    ops->copy_host_to_buffer   = gpu_proxy_copy_h2b;
    ops->copy_buffer_to_host   = gpu_proxy_copy_b2h;
    ops->copy_buffer_to_buffer = gpu_proxy_copy_b2b;
    ops->write_byte            = gpu_proxy_write_byte;
    ops->read_byte             = gpu_proxy_read_byte;
    ops->negotiate_gdr_mode    = gpu_proxy_negotiate;
    ops->stage_to_nic_buffer   = gpu_proxy_stage_to_nic;
    ops->stage_from_nic_buffer = gpu_proxy_stage_from_nic;

    PROXY_LOG("ok (no-CUDA, no-kernel, host-proxy only)");
    return ops;
}
