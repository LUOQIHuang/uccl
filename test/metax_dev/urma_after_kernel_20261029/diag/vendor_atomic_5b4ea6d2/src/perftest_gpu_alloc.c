/*
 * SPDX-License-Identifier: MIT
 * Copyright (c) Huawei Technologies Co., Ltd. 2024-2026. All rights reserved.
 *
 * GPU alloc fallbacks for --gpu-mode={direct,peermem,proxy,none}.
 * CUDA/GDRCopy are loaded via dlopen (no link-time CUDA/gdrcopy deps).
 */

#ifndef _GNU_SOURCE
#define _GNU_SOURCE
#endif
#include <dlfcn.h>
#include <errno.h>
#include <malloc.h>
#include <stdio.h>
#include <stdlib.h>
#include <string.h>
#include <unistd.h>     /* close() */

#include "perftest_cuda_loader.h"
#include "perftest_gdrcopy_loader.h"
#include "perftest_metax_loader.h"
#include "perftest_gpu_alloc.h"

#define GA_LOG(fmt, ...) \
    fprintf(stderr, "[gpu-alloc] %s: " fmt "\n", __func__, ##__VA_ARGS__)

#define PERFTEST_GPU_CPU_FLAG_BYTES ((size_t)64)

static unsigned g_force_fail_mask = 0;

static int alloc_cpu_flag_scratch(perftest_gpu_buffer_t *out)
{
    void *p = NULL;

    if (posix_memalign(&p, PERFTEST_GPU_CPU_FLAG_BYTES, PERFTEST_GPU_CPU_FLAG_BYTES) != 0 ||
        p == NULL) {
        GA_LOG("FAIL: posix_memalign cpu_flag");
        return -1;
    }
    memset(p, 0, PERFTEST_GPU_CPU_FLAG_BYTES);
    out->cpu_flag = p;
    GA_LOG("cpu_flag scratch=%p (D2H fallback)", p);
    return 0;
}

static int try_map_gpu_bar(perftest_gpu_buffer_t *out)
{
    perftest_gdr_mh_t mh = { 0 };
    void *map_raw = NULL;
    void *cpu_base = NULL;

    if (out == NULL || out->gpu_ptr == NULL || out->size == 0) {
        return -1;
    }
    if (perftest_gdrcopy_map(out->gpu_ptr, out->size, &map_raw, &cpu_base,
                             &mh) != 0) {
        GA_LOG("BAR map unavailable, using 1B D2H flag fallback");
        return -1;
    }
    out->bar_map_raw = map_raw;
    out->bar_cpu_base = cpu_base;
    out->bar_map_size = out->size;
    out->bar_mh = mh.h;
    GA_LOG("BAR map OK: gpu=%p cpu=%p size=%zu", out->gpu_ptr, cpu_base,
           out->size);
    return 0;
}

static void unmap_gpu_bar(perftest_gpu_buffer_t *buf)
{
    perftest_gdr_mh_t mh;

    if (buf == NULL || buf->bar_map_raw == NULL) {
        return;
    }
    mh.h = buf->bar_mh;
    (void)perftest_gdrcopy_unmap(buf->bar_map_raw, buf->bar_map_size, mh);
    buf->bar_map_raw = NULL;
    buf->bar_cpu_base = NULL;
    buf->bar_map_size = 0;
    buf->bar_mh = 0;
}

static int setup_cpu_flag_access(perftest_gpu_buffer_t *out)
{
    /* MACA libgdrapi.so.2 relocates mcMemRemap; NVIDIA gdrcopy does not.
     * Opening it from metax_lib/LD_LIBRARY_PATH aborts the process. */
    if (out != NULL && out->strategy == PERFTEST_GPU_ALLOC_METAX) {
        GA_LOG("MetaX: skip gdrcopy BAR, 1B mcMemcpy flag fallback");
        return alloc_cpu_flag_scratch(out);
    }
    if (try_map_gpu_bar(out) == 0) {
        return 0;
    }
    return alloc_cpu_flag_scratch(out);
}

static perftest_gpu_mode_t g_gpu_mode = PERFTEST_GPU_MODE_NONE;
static int g_gpu_dev_id;

void perftest_gpu_set_mode(perftest_gpu_mode_t m)
{
    g_gpu_mode = m;
    GA_LOG("mode = %s", perftest_gpu_mode_name(m));
}

void perftest_gpu_set_device_id(int dev_id)
{
    g_gpu_dev_id = (dev_id < 0) ? 0 : dev_id;
    GA_LOG("device_id = %d", g_gpu_dev_id);
}

int perftest_gpu_get_device_id(void)
{
    return g_gpu_dev_id;
}

perftest_gpu_mode_t perftest_gpu_get_mode(void)
{
    return g_gpu_mode;
}

const char *perftest_gpu_mode_name(perftest_gpu_mode_t m)
{
    switch (m) {
        case PERFTEST_GPU_MODE_NONE:       return "none";
        case PERFTEST_GPU_MODE_PROXY:      return "proxy";
        case PERFTEST_GPU_MODE_PROXY_PURE: return "proxy_pure";
        case PERFTEST_GPU_MODE_PEERMEM:    return "peermem";
        case PERFTEST_GPU_MODE_DIRECT:     return "direct";
        case PERFTEST_GPU_MODE_METAX:      return "metax";
        default:                            return "?";
    }
}

int perftest_gpu_mode_from_string(const char *s, perftest_gpu_mode_t *out)
{
    if (s == NULL || out == NULL) return -1;
    if (strcmp(s, "none")       == 0) { *out = PERFTEST_GPU_MODE_NONE;       return 0; }
    if (strcmp(s, "proxy")      == 0) { *out = PERFTEST_GPU_MODE_PROXY;      return 0; }
    if (strcmp(s, "proxy_pure") == 0) { *out = PERFTEST_GPU_MODE_PROXY_PURE; return 0; }
    if (strcmp(s, "peermem")    == 0) { *out = PERFTEST_GPU_MODE_PEERMEM;    return 0; }
    if (strcmp(s, "direct")     == 0) { *out = PERFTEST_GPU_MODE_DIRECT;     return 0; }
    if (strcmp(s, "metax")      == 0) { *out = PERFTEST_GPU_MODE_METAX;      return 0; }
    return -1;
}

bool perftest_gpu_proxy_stages(void)
{
    /* Only classic proxy stages in BW/LAT timed paths; proxy_pure does not. */
    return g_gpu_mode == PERFTEST_GPU_MODE_PROXY;
}

void perftest_gpu_alloc_set_force_fail_mask(unsigned mask)
{
    g_force_fail_mask = mask;
    GA_LOG("force_fail_mask=0x%x", mask);
}

static int forced_fail(perftest_gpu_alloc_strategy_t s)
{
    return (g_force_fail_mask >> (unsigned)s) & 0x1u;
}

const char *perftest_gpu_alloc_strategy_name(perftest_gpu_alloc_strategy_t s)
{
    switch (s) {
        case PERFTEST_GPU_ALLOC_GDR:        return "GDR";
        case PERFTEST_GPU_ALLOC_PEERMEM:    return "PEERMEM";
        case PERFTEST_GPU_ALLOC_HOST_PROXY: return "HOST_PROXY";
        case PERFTEST_GPU_ALLOC_MEMALIGN:   return "MEMALIGN";
        case PERFTEST_GPU_ALLOC_METAX:      return "METAX";
        case PERFTEST_GPU_ALLOC_NONE:
        default:                            return "NONE";
    }
}

/* --gpu-mode=direct: cuMemAlloc + dma-buf fd (register_seg_dmabuf may still fail). */
static int try_alloc_gdr(size_t size, size_t alignment,
                         perftest_gpu_buffer_t *out)
{
    void    *dptr = NULL;
    int      fd   = -1;
    uint64_t off  = 0;

    (void)alignment;

    if (forced_fail(PERFTEST_GPU_ALLOC_GDR)) {
        GA_LOG("forced fail by test mask");
        return -1;
    }

    if (perftest_cuda_loader_init() != 0) {
        GA_LOG("FAIL: libcuda not available");
        return -1;
    }
    if (perftest_cuda_ctx_create(0) != 0) {
        GA_LOG("FAIL: cuCtxCreate");
        return -1;
    }
    if (perftest_cuda_mem_alloc(size, &dptr) != 0) {
        GA_LOG("FAIL: cuMemAlloc");
        return -1;
    }
    if (perftest_cuda_get_dmabuf_fd(dptr, size, &fd, &off) != 0) {
        GA_LOG("FAIL: cuMemGetHandleForAddressRange (CUDA < 12.2?)");
        perftest_cuda_mem_free(dptr);
        return -1;
    }

    memset(out, 0, sizeof(*out));
    out->addr = dptr;
    out->gpu_ptr = dptr;
    out->size = size;
    out->dmabuf_fd = fd;
    out->dmabuf_offset = off;
    out->strategy = PERFTEST_GPU_ALLOC_GDR;
    if (setup_cpu_flag_access(out) != 0) {
        close(fd);
        perftest_cuda_mem_free(dptr);
        memset(out, 0, sizeof(*out));
        return -1;
    }

    GA_LOG("OK: dptr=%p fd=%d size=%zu bar=%s", dptr, fd, size,
           out->bar_cpu_base != NULL ? "mapped" : "d2h-fallback");
    return 0;
}
/*
static int has_kernel_module(const char *name)
{
    FILE *fp = fopen("/proc/modules", "r");
    char  line[256];
    int   found = 0;
    if (fp == NULL) return 0;
    while (fgets(line, sizeof(line), fp) != NULL) {
        if (strncmp(line, name, strlen(name)) == 0 &&
            (line[strlen(name)] == ' ' || line[strlen(name)] == '\t')) {
            found = 1;
            break;
        }
    }
    fclose(fp);
    return found;
}
*/
/* nvidia_p2p_get_pages requires 64KB-aligned length; pad alloc accordingly. */
#define PERFTEST_GPU_SEG_ALIGN ((size_t)(64u * 1024u))
static size_t align_up_size(size_t size, size_t align)
{
    return (size + align - 1) & ~(align - 1);
}

/* --gpu-mode=peermem */
static int try_alloc_peermem(size_t size, size_t alignment,
                             perftest_gpu_buffer_t *out)
{
    void *dptr = NULL;
    size_t alloc_size = align_up_size(size, PERFTEST_GPU_SEG_ALIGN);
    (void)alignment;

    if (forced_fail(PERFTEST_GPU_ALLOC_PEERMEM)) {
        GA_LOG("forced fail by test mask");
        return -1;
    }

    if (perftest_cuda_loader_init() != 0) {
        GA_LOG("FAIL: libcuda.so.1 not present");
        return -1;
    }
    /*
    if (!has_kernel_module("nvidia_peermem") &&
        !has_kernel_module("amd_peermem")) {
        GA_LOG("FAIL: no peer-mem kernel module "
               "(modprobe nvidia_peermem or amd_peermem)");
        return -1;
    }
    */
    if (perftest_cuda_ctx_create(0) != 0) {
        GA_LOG("FAIL: cuCtxCreate");
        return -1;
    }
    if (perftest_cuda_mem_alloc(alloc_size, &dptr) != 0) {
        GA_LOG("FAIL: cuMemAlloc");
        return -1;
    }

    /* Required before nvidia_p2p_get_pages on the modern token-less path. */
    if (perftest_cuda_set_sync_memops(dptr) != 0) {
        GA_LOG("FAIL: SYNC_MEMOPS on dptr=%p", dptr);
        (void)perftest_cuda_mem_free(dptr);
        return -1;
    }

    memset(out, 0, sizeof(*out));
    out->addr = dptr;
    out->gpu_ptr = dptr;
    out->size = alloc_size;
    out->dmabuf_fd = -1;
    out->strategy = PERFTEST_GPU_ALLOC_PEERMEM;
    if (setup_cpu_flag_access(out) != 0) {
        (void)perftest_cuda_mem_free(dptr);
        memset(out, 0, sizeof(*out));
        return -1;
    }

    GA_LOG("OK: dptr=%p req=%zu alloc=%zu bar=%s", dptr, size, alloc_size,
           out->bar_cpu_base != NULL ? "mapped" : "d2h-fallback");
    return 0;
}

/* Kernel MetaX acquire_mem aligns to PAGE_SIZE, not NVIDIA 64KB. */
#define PERFTEST_METAX_SEG_ALIGN ((size_t)4096u)

/* --gpu-mode=metax: MACA mcMalloc GPU VA; kernel pins via gpu_vendor=metax. */
static int try_alloc_metax(size_t size, size_t alignment,
                           perftest_gpu_buffer_t *out)
{
    void *dptr = NULL;
    size_t alloc_size = align_up_size(size, PERFTEST_METAX_SEG_ALIGN);

    (void)alignment;

    if (forced_fail(PERFTEST_GPU_ALLOC_METAX)) {
        GA_LOG("forced fail by test mask");
        return -1;
    }
    if (perftest_metax_loader_init() != 0) {
        GA_LOG("FAIL: libmcruntime not present");
        return -1;
    }
    if (perftest_metax_set_device(g_gpu_dev_id) != 0) {
        GA_LOG("FAIL: mcSetDevice(%d)", g_gpu_dev_id);
        return -1;
    }
    if (perftest_metax_mem_alloc(alloc_size, &dptr) != 0) {
        GA_LOG("FAIL: mcMalloc");
        return -1;
    }

    memset(out, 0, sizeof(*out));
    out->addr = dptr;
    out->gpu_ptr = dptr;
    out->size = alloc_size;
    out->dmabuf_fd = -1;
    out->strategy = PERFTEST_GPU_ALLOC_METAX;
    if (setup_cpu_flag_access(out) != 0) {
        (void)perftest_metax_mem_free(dptr);
        memset(out, 0, sizeof(*out));
        return -1;
    }

    GA_LOG("OK: dptr=%p req=%zu alloc=%zu device=%d bar=%s",
           dptr, size, alloc_size, g_gpu_dev_id,
           out->bar_cpu_base != NULL ? "mapped" : "d2h-fallback");
    return 0;
}

/* --gpu-mode=proxy: GPU device buf + host shadow for URMA register. */
static int try_alloc_host_proxy(size_t size, size_t alignment,
                                perftest_gpu_buffer_t *out)
{
    void       *gpu_ptr = NULL;
    void       *shadow  = NULL;
    const char *sim;
    int         have_cuda;

    if (forced_fail(PERFTEST_GPU_ALLOC_HOST_PROXY)) {
        GA_LOG("forced fail by test mask");
        return -1;
    }

    have_cuda = (perftest_cuda_loader_init() == 0);
    sim       = getenv("PERFTEST_GPU_ALLOC_ALLOW_HOST_PROXY_SIM");

    if (!have_cuda && (sim == NULL || strcmp(sim, "1") != 0)) {
        GA_LOG("FAIL: libcuda.so.1 not present "
               "and PERFTEST_GPU_ALLOC_ALLOW_HOST_PROXY_SIM!=1");
        return -1;
    }

    if (have_cuda) {
        if (perftest_cuda_ctx_create(0) != 0) {
            GA_LOG("FAIL: cuCtxCreate");
            return -1;
        }
        if (perftest_cuda_mem_alloc(size, &gpu_ptr) != 0) {
            GA_LOG("FAIL: cuMemAlloc");
            return -1;
        }
        if (perftest_cuda_mem_alloc_host(size, &shadow) != 0) {
            GA_LOG("FAIL: cuMemAllocHost");
            perftest_cuda_mem_free(gpu_ptr);
            return -1;
        }
    } else {
        /* Opt-in sim: both sides are host memalign (unit tests / no CUDA). */
        GA_LOG("simulation mode: memalign for gpu_ptr and shadow");
        gpu_ptr = memalign(alignment, size);
        shadow = memalign(alignment, size);
        if (gpu_ptr == NULL || shadow == NULL) {
            free(gpu_ptr);
            free(shadow);
            GA_LOG("FAIL: ENOMEM");
            return -1;
        }
        memset(gpu_ptr, 0, size);
        memset(shadow, 0, size);
    }

    memset(out, 0, sizeof(*out));
    out->addr = shadow; /* URMA registers host shadow */
    out->gpu_ptr = gpu_ptr;
    out->size = size;
    out->dmabuf_fd = -1;
    out->strategy = PERFTEST_GPU_ALLOC_HOST_PROXY;

    GA_LOG("OK: gpu_ptr=%p shadow=%p size=%zu have_cuda=%d",
           gpu_ptr, shadow, size, have_cuda);
    return 0;
}

/* Host memalign fallback. */
static int try_alloc_memalign(size_t size, size_t alignment,
                              perftest_gpu_buffer_t *out)
{
    out->addr = memalign(alignment, size);
    if (out->addr == NULL) {
        GA_LOG("FAIL: ENOMEM allocating %zu bytes", size);
        return -1;
    }
    memset(out->addr, 0, size);
    out->size = size;
    out->dmabuf_fd = -1;
    out->dmabuf_offset = 0;
    out->gpu_ptr = NULL;
    out->strategy = PERFTEST_GPU_ALLOC_MEMALIGN;

    GA_LOG("OK: addr=%p size=%zu (host fallback)", out->addr, size);
    return 0;
}

/* Select strategy from --gpu-mode; always fall back to memalign on failure. */
int perftest_gpu_alloc_buffer(size_t size, size_t alignment,
                              perftest_gpu_buffer_t *out)
{
    if (out == NULL || size == 0) {
        GA_LOG("invalid arg out=%p size=%zu", (void *)out, size);
        errno = EINVAL;
        return -1;
    }
    if (alignment < 4096) {
        alignment = 4096;
    }
    memset(out, 0, sizeof(*out));

    GA_LOG("requested size=%zu align=%zu mode=%s force_fail_mask=0x%x",
           size, alignment, perftest_gpu_mode_name(g_gpu_mode), g_force_fail_mask);

    switch (g_gpu_mode) {
        case PERFTEST_GPU_MODE_DIRECT:
            if (try_alloc_gdr(size, alignment, out) == 0) {
                GA_LOG("DONE via %s",
                       perftest_gpu_alloc_strategy_name(out->strategy));
                return 0;
            }
            GA_LOG("mode=direct failed, fallback to memalign");
            break;
        case PERFTEST_GPU_MODE_PEERMEM:
            if (try_alloc_peermem(size, alignment, out) == 0) {
                GA_LOG("DONE via %s",
                       perftest_gpu_alloc_strategy_name(out->strategy));
                return 0;
            }
            GA_LOG("mode=peermem failed, fallback to memalign");
            break;
        case PERFTEST_GPU_MODE_METAX:
            if (try_alloc_metax(size, alignment, out) == 0) {
                GA_LOG("DONE via %s",
                       perftest_gpu_alloc_strategy_name(out->strategy));
                return 0;
            }
            GA_LOG("mode=metax failed, fallback to memalign");
            break;
        case PERFTEST_GPU_MODE_PROXY:
        case PERFTEST_GPU_MODE_PROXY_PURE:
            if (try_alloc_host_proxy(size, alignment, out) == 0) {
                GA_LOG("DONE via %s (mode=%s stage=%s)",
                       perftest_gpu_alloc_strategy_name(out->strategy),
                       perftest_gpu_mode_name(g_gpu_mode),
                       perftest_gpu_proxy_stages() ? "on" : "off");
                return 0;
            }
            GA_LOG("mode=%s failed, fallback to memalign",
                   perftest_gpu_mode_name(g_gpu_mode));
            break;
        case PERFTEST_GPU_MODE_NONE:
        default:
            break;
    }

    if (try_alloc_memalign(size, alignment, out) == 0) {
        GA_LOG("DONE via %s", perftest_gpu_alloc_strategy_name(out->strategy));
        return 0;
    }

    GA_LOG("FATAL: even memalign failed (OOM)");
    return -1;
}

/* HOST_PROXY + --gpu-mode=proxy only. proxy_pure and other modes are no-ops. */
int perftest_gpu_stage_to_nic(perftest_gpu_buffer_t *buf, size_t len)
{
    if (buf == NULL || buf->size == 0 || len > buf->size) {
        GA_LOG("invalid arg buf=%p len=%zu", (void *)buf, len);
        return -1;
    }
    if (!perftest_gpu_proxy_stages() ||
        buf->strategy != PERFTEST_GPU_ALLOC_HOST_PROXY) {
        return 0;
    }
    if (buf->gpu_ptr == NULL || buf->addr == NULL) {
        GA_LOG("FAIL: HOST_PROXY but gpu_ptr/shadow missing");
        return -1;
    }
    if (perftest_cuda_is_loaded()) {
        return perftest_cuda_memcpy_d2h(buf->addr, buf->gpu_ptr, len);
    }
    memcpy(buf->addr, buf->gpu_ptr, len);
    return 0;
}

int perftest_gpu_stage_from_nic(perftest_gpu_buffer_t *buf, size_t len)
{
    if (buf == NULL || buf->size == 0 || len > buf->size) {
        GA_LOG("invalid arg buf=%p len=%zu", (void *)buf, len);
        return -1;
    }
    if (!perftest_gpu_proxy_stages() ||
        buf->strategy != PERFTEST_GPU_ALLOC_HOST_PROXY) {
        return 0;
    }
    if (buf->gpu_ptr == NULL || buf->addr == NULL) {
        GA_LOG("FAIL: HOST_PROXY but gpu_ptr/shadow missing");
        return -1;
    }
    if (perftest_cuda_is_loaded()) {
        return perftest_cuda_memcpy_h2d(buf->gpu_ptr, buf->addr, len);
    }
    memcpy(buf->gpu_ptr, buf->addr, len);
    return 0;
}

int perftest_gpu_stage_to_nic_range(perftest_gpu_buffer_t *buf,
                                    size_t off, size_t len)
{
    if (buf == NULL || off + len > buf->size)
        return -1;

    if (!perftest_gpu_proxy_stages() ||
        buf->strategy != PERFTEST_GPU_ALLOC_HOST_PROXY)
        return 0;

    if (perftest_cuda_is_loaded()) {
        return perftest_cuda_memcpy_d2h((char *)buf->addr + off,
                                        (void *)((uintptr_t)buf->gpu_ptr + off),
                                        len);
    }
    memcpy((char *)buf->addr + off,
           (void *)((uintptr_t)buf->gpu_ptr + off), len);
    return 0;
}

int perftest_gpu_stage_from_nic_range(perftest_gpu_buffer_t *buf,
                                      size_t off, size_t len)
{
    if (buf == NULL || off + len > buf->size)
        return -1;

    if (!perftest_gpu_proxy_stages() ||
        buf->strategy != PERFTEST_GPU_ALLOC_HOST_PROXY)
        return 0;

    if (perftest_cuda_is_loaded()) {
        return perftest_cuda_memcpy_h2d((void *)((uintptr_t)buf->gpu_ptr + off),
                                        (char *)buf->addr + off,
                                        len);
    }
    memcpy((void *)((uintptr_t)buf->gpu_ptr + off),
           (char *)buf->addr + off, len);
    return 0;
}

bool perftest_gpu_is_device_va(const perftest_gpu_buffer_t *buf)
{
    if (buf == NULL) {
        return false;
    }
    return buf->strategy == PERFTEST_GPU_ALLOC_PEERMEM ||
           buf->strategy == PERFTEST_GPU_ALLOC_GDR ||
           buf->strategy == PERFTEST_GPU_ALLOC_METAX;
}

bool perftest_gpu_has_bar_map(const perftest_gpu_buffer_t *buf)
{
    return buf != NULL && buf->bar_cpu_base != NULL && buf->gpu_ptr != NULL;
}

void *perftest_gpu_bar_from_gpu(const perftest_gpu_buffer_t *buf, void *gpu_va)
{
    uintptr_t off;

    if (!perftest_gpu_has_bar_map(buf) || gpu_va == NULL) {
        return NULL;
    }
    off = (uintptr_t)gpu_va - (uintptr_t)buf->gpu_ptr;
    if (off >= buf->bar_map_size) {
        return NULL;
    }
    return (char *)buf->bar_cpu_base + off;
}

bool perftest_gpu_needs_cpu_flag(const perftest_gpu_buffer_t *buf)
{
    if (buf == NULL || buf->cpu_flag == NULL) {
        return false;
    }
    if (perftest_gpu_has_bar_map(buf)) {
        return false;
    }
    return buf->strategy == PERFTEST_GPU_ALLOC_PEERMEM ||
           buf->strategy == PERFTEST_GPU_ALLOC_GDR ||
           buf->strategy == PERFTEST_GPU_ALLOC_METAX;
}

static int gpu_memcpy_d2h(void *host, void *dev, size_t n)
{
    if (perftest_metax_is_loaded()) {
        return perftest_metax_memcpy_d2h(host, dev, n);
    }
    if (perftest_cuda_is_loaded()) {
        return perftest_cuda_memcpy_d2h(host, dev, n);
    }
    return -1;
}

static int gpu_memcpy_h2d(void *dev, const void *host, size_t n)
{
    if (perftest_metax_is_loaded()) {
        return perftest_metax_memcpy_h2d(dev, host, n);
    }
    if (perftest_cuda_is_loaded()) {
        return perftest_cuda_memcpy_h2d(dev, host, n);
    }
    return -1;
}

int perftest_gpu_flag_load(perftest_gpu_buffer_t *buf, void *gpu_flag_va, char *out)
{
    if (!perftest_gpu_needs_cpu_flag(buf) || gpu_flag_va == NULL || out == NULL) {
        return -1;
    }
    if (gpu_memcpy_d2h(buf->cpu_flag, gpu_flag_va, 1) != 0) {
        return -1;
    }
    *out = *(volatile char *)buf->cpu_flag;
    return 0;
}

int perftest_gpu_flag_store(perftest_gpu_buffer_t *buf, void *gpu_flag_va, char val)
{
    if (!perftest_gpu_needs_cpu_flag(buf) || gpu_flag_va == NULL) {
        return -1;
    }
    *(volatile char *)buf->cpu_flag = val;
    return gpu_memcpy_h2d(gpu_flag_va, buf->cpu_flag, 1);
}

void perftest_gpu_free_buffer(perftest_gpu_buffer_t *buf)
{
    if (buf == NULL) return;

    GA_LOG("releasing strategy=%s addr=%p gpu_ptr=%p cpu_flag=%p bar=%p size=%zu fd=%d",
           perftest_gpu_alloc_strategy_name(buf->strategy),
           buf->addr, buf->gpu_ptr, buf->cpu_flag, buf->bar_cpu_base,
           buf->size, buf->dmabuf_fd);

    unmap_gpu_bar(buf);

    if (buf->cpu_flag != NULL) {
        free(buf->cpu_flag);
        buf->cpu_flag = NULL;
    }

    switch (buf->strategy) {
        case PERFTEST_GPU_ALLOC_GDR:
            if (buf->dmabuf_fd >= 0) {
                close(buf->dmabuf_fd);
            }
            if (perftest_cuda_is_loaded()) {
                perftest_cuda_mem_free(buf->gpu_ptr);
            }
            break;
        case PERFTEST_GPU_ALLOC_PEERMEM:
            if (perftest_cuda_is_loaded()) {
                perftest_cuda_mem_free(buf->gpu_ptr);
            }
            break;
        case PERFTEST_GPU_ALLOC_METAX:
            (void)perftest_metax_mem_free(buf->gpu_ptr);
            break;
        case PERFTEST_GPU_ALLOC_HOST_PROXY:
            if (perftest_cuda_is_loaded()) {
                perftest_cuda_mem_free(buf->gpu_ptr);
                perftest_cuda_mem_free_host(buf->addr);
            } else {
                free(buf->gpu_ptr);
                free(buf->addr);
            }
            break;
        case PERFTEST_GPU_ALLOC_MEMALIGN:
            free(buf->addr);
            break;
        default:
            break;
    }
    memset(buf, 0, sizeof(*buf));
}
