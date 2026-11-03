/*
 * SPDX-License-Identifier: MIT
 * Copyright (c) Huawei Technologies Co., Ltd. 2024-2026. All rights reserved.
 *
 * Legacy stub / test helper: perftest_mem_cuda.c
 */
#include <stdio.h>
#include <stdlib.h>
#include <string.h>

#include "perftest_memory.h"

typedef struct cuda_priv {
    int   gpu_id;          /* TODO: 改为 CUdevice */
    void *cuctx_opaque;    /* TODO: 改为 CUcontext */
    int   use_dmabuf;      /* CLI 显式要求 dma-buf */
    int   driver_version;
    /*
     * 下面这几个字段 PROXY 模式下要用到, 当前是 stub, 不分配也不操作:
     */
    void *d_ptr_opaque;    /* TODO: CUdeviceptr cuMemAlloc 结果 */
    void *shadow_addr;     /* TODO: cuMemAllocHost 结果 */
    size_t buffer_size;
} cuda_priv_t;

static int cuda_init(perftest_memory_ops_t *self, perftest_config_t *cfg)
{
    (void)self; (void)cfg;
    
    fprintf(stderr, "[perftest-mem][cuda] init: stub ok (no real cuInit)\n");
    return 0;
}

static void cuda_destroy(perftest_memory_ops_t *self)
{
    (void)self;
    /* TODO: cuCtxDestroy(priv->cuctx); free(priv); */
    fprintf(stderr, "[perftest-mem][cuda] destroy: stub ok\n");
}

static int cuda_alloc_buffer(perftest_memory_ops_t *self,
                             uint64_t size, uint64_t alignment,
                             void **out_addr,
                             int *out_dmabuf_fd,
                             uint64_t *out_dmabuf_offset)
{
    (void)alignment;
    
    if (out_addr)          *out_addr = NULL;
    if (out_dmabuf_fd)     *out_dmabuf_fd = -1;
    if (out_dmabuf_offset) *out_dmabuf_offset = 0;
    fprintf(stderr,
            "[perftest-mem][cuda] alloc_buffer stub: size=%lu mode=%d (0=AUTO 1=DIRECT 2=PROXY 3=OFF)\n",
            (unsigned long)size, (int)self->gdr_mode);
    return 0;
}

static void cuda_free_buffer(perftest_memory_ops_t *self,
                             void *addr, int dmabuf_fd, uint64_t size)
{
    (void)self; (void)addr; (void)dmabuf_fd; (void)size;
    /*
     * TODO 真实现:
     *   if (dmabuf_fd >= 0) close(dmabuf_fd);
     *   cuMemFree((CUdeviceptr)addr);
     */
    fprintf(stderr, "[perftest-mem][cuda] free_buffer: stub ok\n");
}

/*
 * 三个 copy_* 在真实现里就是:
 *   cuMemcpyHtoD / cuMemcpyDtoH / cuMemcpyDtoD
 * 加错误码转换. 现阶段只 print + 返回 dst.
 */
static void *cuda_copy_h2b(perftest_memory_ops_t *self,
                           void *dst, const void *src, size_t n)
{
    (void)self; (void)src;
    fprintf(stderr, "[perftest-mem][cuda] copy_h2b stub: %zu bytes -> dev\n", n);
    return dst;
}

static void *cuda_copy_b2h(perftest_memory_ops_t *self,
                           void *dst, const void *src, size_t n)
{
    (void)self; (void)src;
    fprintf(stderr, "[perftest-mem][cuda] copy_b2h stub: %zu bytes <- dev\n", n);
    return dst;
}

static void *cuda_copy_b2b(perftest_memory_ops_t *self,
                           void *dst, const void *src, size_t n)
{
    (void)self; (void)src;
    fprintf(stderr, "[perftest-mem][cuda] copy_b2b stub: %zu bytes dev->dev\n", n);
    return dst;
}

static void cuda_write_byte(perftest_memory_ops_t *self, void *addr,
                            uint64_t off, uint8_t v)
{
    (void)self; (void)addr; (void)off; (void)v;
    /*
     * 注意: latency 测试的 sentinel 字节不能直接写到 GPU 显存!
     * 真实现会让后端在 init 时额外 cuMemAllocHost 一小块 host pinned memory,
     * 这个 host shadow 由 NIC 与 CPU 共同访问做 polling. write_byte/read_byte
     * 走 host shadow, 数据 buffer 仍走 GPU.
     */
    fprintf(stderr, "[perftest-mem][cuda] write_byte stub off=%lu v=%u\n",
            (unsigned long)off, (unsigned)v);
}

static uint8_t cuda_read_byte(perftest_memory_ops_t *self, const void *addr,
                              uint64_t off)
{
    (void)self; (void)addr; (void)off;
    fprintf(stderr, "[perftest-mem][cuda] read_byte stub off=%lu -> 0\n",
            (unsigned long)off);
    return 0;
}

static int cuda_negotiate(perftest_memory_ops_t *self, void *urma_dev_opaque)
{
    
    (void)urma_dev_opaque;
    self->gdr_mode = PERFTEST_GDR_HOST_PROXY;
    fprintf(stderr,
            "[perftest-mem][cuda] negotiate: driver dma-buf NOT supported -> "
            "host-proxy fallback (all data staged via cuMemcpyDtoH/HtoD)\n");
    return 0;
}

static int cuda_stage_to_nic(perftest_memory_ops_t *self, void *shadow_addr,
                             void *gpu_addr, size_t len)
{
    (void)shadow_addr; (void)gpu_addr;
    /*
     * PROXY 路径下, 在每次 post send 之前调.
     *   真实现: cuMemcpyDtoH(shadow_addr, (CUdeviceptr)gpu_addr, len);
     * DIRECT 模式下, NIC 直接读 GPU, 不调这个; 但即便误调也是 noop.
     */
    if (self->gdr_mode != PERFTEST_GDR_HOST_PROXY) {
        return 0;
    }
    fprintf(stderr,
            "[perftest-mem][cuda] stage_to_nic stub: would cuMemcpyDtoH(shadow,gpu,%zu)\n",
            len);
    return 0;
}

static int cuda_stage_from_nic(perftest_memory_ops_t *self, void *gpu_addr,
                               void *shadow_addr, size_t len)
{
    (void)gpu_addr; (void)shadow_addr;
    /*
     * PROXY 路径下, 在每次 recv 完成后调.
     *   真实现: cuMemcpyHtoD((CUdeviceptr)gpu_addr, shadow_addr, len);
     */
    if (self->gdr_mode != PERFTEST_GDR_HOST_PROXY) {
        return 0;
    }
    fprintf(stderr,
            "[perftest-mem][cuda] stage_from_nic stub: would cuMemcpyHtoD(gpu,shadow,%zu)\n",
            len);
    return 0;
}

perftest_memory_ops_t *perftest_cuda_mem_create(perftest_config_t *cfg)
{
    (void)cfg;
    perftest_memory_ops_t *ops = (perftest_memory_ops_t *)calloc(1, sizeof(*ops));
    if (!ops) {
        fprintf(stderr, "[perftest-mem][cuda] create: oom\n");
        return NULL;
    }
    cuda_priv_t *priv = (cuda_priv_t *)calloc(1, sizeof(*priv));
    if (!priv) {
        free(ops);
        return NULL;
    }
    /* TODO: 从 cfg 拷贝 gpu_id / use_dmabuf 等. */
    ops->type                  = PERFTEST_MEM_CUDA;
    ops->gdr_mode              = PERFTEST_GDR_AUTO;   /* 等 negotiate 改写 */
    ops->priv                  = priv;
    ops->init                  = cuda_init;
    ops->destroy               = cuda_destroy;
    ops->alloc_buffer          = cuda_alloc_buffer;
    ops->free_buffer           = cuda_free_buffer;
    ops->copy_host_to_buffer   = cuda_copy_h2b;
    ops->copy_buffer_to_host   = cuda_copy_b2h;
    ops->copy_buffer_to_buffer = cuda_copy_b2b;
    ops->write_byte            = cuda_write_byte;
    ops->read_byte             = cuda_read_byte;
    ops->negotiate_gdr_mode    = cuda_negotiate;
    ops->stage_to_nic_buffer   = cuda_stage_to_nic;
    ops->stage_from_nic_buffer = cuda_stage_from_nic;
    fprintf(stderr, "[perftest-mem][cuda] create: stub ok\n");
    return ops;
}
