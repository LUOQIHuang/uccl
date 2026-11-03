/*
 * SPDX-License-Identifier: MIT
 * Copyright (c) Huawei Technologies Co., Ltd. 2024-2026. All rights reserved.
 *
 * dlopen/dlsym wrappers for libcuda.so.1 (no cuda.h at build time).
 */

#ifndef _GNU_SOURCE
#define _GNU_SOURCE
#endif

#include <dlfcn.h>
#include <errno.h>
#include <stdint.h>
#include <stdio.h>
#include <stdlib.h>
#include <string.h>
#include <unistd.h>

#include "perftest_cuda_loader.h"

#define CL_LOG(fmt, ...) \
    fprintf(stderr, "[cuda-loader] %s: " fmt "\n", __func__, ##__VA_ARGS__)

/* ---- CUDA Driver API ABI 速写, 与 cuda.h 内容一致 (CUDA 12.x) ---- */
typedef int      CUresult;          /* cuda.h: enum CUresult */
typedef int      CUdevice;
typedef void    *CUcontext;
typedef uint64_t CUdeviceptr;
/*
 * FIX: 单独给 CUpointer_attribute 一个 typedef (而不是内联写 int), 纯粹是
 * 规范化/可读性收益 —— ABI 上跟 int 完全一样 (cuda.h 里就是个普通 enum,
 * 4 字节, 不会因为换个 typedef 名字改变调用约定), 之前那次 EINVAL 不是
 * 这个类型名字的锅。
 */
typedef int      CUpointer_attribute;
#define CUDA_SUCCESS                                 0
#define CU_CTX_MAP_HOST                              0x08
#define CU_MEM_RANGE_HANDLE_TYPE_DMA_BUF_FD          0x1
/*
 * CUpointer_attribute 里跟 GPUDirect RDMA "pin 之前的握手" 相关的那一个。
 * 数值来自 cuda.h 的稳定 ABI 枚举 (CU_POINTER_ATTRIBUTE_SYNC_MEMOPS = 6),
 * 跟本文件其它属性常量 (PTR_ATTR_MEMORY_TYPE=2/IS_MANAGED=8/
 * DEVICE_ORDINAL=9/RANGE_START_ADDR=11/RANGE_SIZE=12, 见 dump_cuda_ptr())
 * 是同一套编号体系, 已经跟官方文档核对过。
 */
#define CU_POINTER_ATTRIBUTE_SYNC_MEMOPS             6
/* 在 cuda.h 里 cuMemGetHandleForAddressRange 的签名是:
 *   CUresult cuMemGetHandleForAddressRange(
 *       void *handle, CUdeviceptr dptr, size_t size,
 *       CUmemRangeHandleType handleType, unsigned long long flags);
 */

_Static_assert(sizeof(CUdeviceptr) == 8, "CUdeviceptr must be 64-bit");
_Static_assert(sizeof(uintptr_t) == 8, "uintptr_t must be 64-bit");

/* ---- 函数指针类型 ---- */
typedef CUresult (*pfn_cuInit)(unsigned int Flags);
typedef CUresult (*pfn_cuDriverGetVersion)(int *driverVersion);
typedef CUresult (*pfn_cuDeviceGet)(CUdevice *device, int ordinal);
typedef CUresult (*pfn_cuDeviceGetCount)(int *count);
typedef CUresult (*pfn_cuDeviceGetName)(char *name, int len, CUdevice dev);
typedef CUresult (*pfn_cuCtxCreate)(CUcontext *pctx, unsigned int flags, CUdevice dev);
typedef CUresult (*pfn_cuCtxDestroy)(CUcontext ctx);
typedef CUresult (*pfn_cuCtxSetCurrent)(CUcontext ctx);
typedef CUresult (*pfn_cuCtxGetCurrent)(CUcontext *pctx);
typedef CUresult (*pfn_cuMemAlloc)(CUdeviceptr *dptr, size_t bytesize);
typedef CUresult (*pfn_cuMemFree)(CUdeviceptr dptr);
typedef CUresult (*pfn_cuMemAllocHost)(void **pp, size_t bytesize);
typedef CUresult (*pfn_cuMemFreeHost)(void *p);
typedef CUresult (*pfn_cuMemcpyDtoH)(void *dstHost, CUdeviceptr srcDevice, size_t ByteCount);
typedef CUresult (*pfn_cuMemcpyHtoD)(CUdeviceptr dstDevice, const void *srcHost, size_t ByteCount);
typedef CUresult (*pfn_cuMemGetHandleForAddressRange)(
    void *handle, CUdeviceptr dptr, size_t size,
    int handleType, unsigned long long flags);
typedef CUresult (*pfn_cuPointerSetAttribute)(
    const void *value, CUpointer_attribute attribute, CUdeviceptr ptr);
/*
 * FIX: cuGetErrorName/cuGetErrorString —— 之前排查 cuPointerSetAttribute
 * 返回 1 (CUDA_ERROR_INVALID_VALUE) 只能靠猜数字, 加上这两个之后所有
 * CL_LOG 都能顺手打出符号名 + 官方文字描述, 以后再出现任何 CUresult != 0
 * 都不用再去翻 cuda.h 对照表。
 */
typedef CUresult (*pfn_cuGetErrorName)(CUresult error, const char **pStr);
typedef CUresult (*pfn_cuGetErrorString)(CUresult error, const char **pStr);

/* ---- 全局状态 ---- */
static int        g_loaded;
static int        g_ctx_owned;
static void      *g_libcuda;
static CUcontext  g_ctx;

#define DECL_SYM(name) static pfn_##name g_##name
DECL_SYM(cuInit);
DECL_SYM(cuDriverGetVersion);
DECL_SYM(cuDeviceGet);
DECL_SYM(cuDeviceGetCount);
DECL_SYM(cuDeviceGetName);
DECL_SYM(cuCtxCreate);
DECL_SYM(cuCtxDestroy);
DECL_SYM(cuCtxSetCurrent);
DECL_SYM(cuCtxGetCurrent);
DECL_SYM(cuMemAlloc);
DECL_SYM(cuMemFree);
DECL_SYM(cuMemAllocHost);
DECL_SYM(cuMemFreeHost);
DECL_SYM(cuMemcpyDtoH);
DECL_SYM(cuMemcpyHtoD);
DECL_SYM(cuMemGetHandleForAddressRange);
DECL_SYM(cuPointerSetAttribute);
DECL_SYM(cuGetErrorName);
DECL_SYM(cuGetErrorString);

/*
 * FIX: 统一的错误打印 helper, 优先用 cuGetErrorName/cuGetErrorString
 * (需要 loader 已经把这两个符号解出来); 解不出来 (极老驱动) 就退化成
 * 纯数字, 不会崩。
 */
static void cl_log_cuda_error(const char *what, CUresult rc)
{
    const char *name = NULL;
    const char *desc = NULL;

    if (g_cuGetErrorName != NULL)
        (void)g_cuGetErrorName(rc, &name);
    if (g_cuGetErrorString != NULL)
        (void)g_cuGetErrorString(rc, &desc);

    fprintf(stderr, "[cuda-loader] %s -> %d (%s): %s\n",
            what, (int)rc,
            name ? name : "unknown",
            desc ? desc : "unknown");
}

//Change the context to current thread
static int perftest_cuda_make_ctx_current(void)
{
    CUresult rc;

    if (!g_loaded) {
        CL_LOG("loader not initialised");
        return -1;
    }

    if (!g_ctx) {
        CL_LOG("CUDA ctx not created");
        return -1;
    }

    rc = g_cuCtxSetCurrent(g_ctx);
    if (rc != CUDA_SUCCESS) {
        CL_LOG("cuCtxSetCurrent -> %d", (int)rc);
        return -1;
    }

    return 0;
}

int perftest_cuda_set_current_ctx(void)
{
    CUresult rc;

    if (!g_loaded || !g_ctx)
        return -1;

    rc = g_cuCtxSetCurrent(g_ctx);
    if (rc != CUDA_SUCCESS) {
        CL_LOG("cuCtxSetCurrent -> %d", (int)rc);
        return -1;
    }

    return 0;
}

/*
 * 部分 CUDA Driver API 名字带 _v2 后缀 (ABI stable handle). 这里两个候选名
 * 都尝试, 与 linux-rdma 的做法一致.
 */


static void *dlsym_prefer_v2(void *lib,
                             const char *legacy,
                             const char *v2)
{
    void *p = NULL;

    if (v2 != NULL)
        p = dlsym(lib, v2);

    if (p == NULL)
        p = dlsym(lib, legacy);

    return p;
}


static void *dlsym2(void *lib, const char *primary, const char *fallback)
{
    void *p = dlsym(lib, primary);
    if (p == NULL && fallback != NULL) {
        p = dlsym(lib, fallback);
    }
    return p;
}

int perftest_cuda_loader_init(void)
{
    if (g_loaded) {
        return 0;
    }
    g_libcuda = dlopen("libcuda.so.1", RTLD_LAZY | RTLD_GLOBAL);
    if (g_libcuda == NULL) {
        CL_LOG("dlopen(libcuda.so.1) failed: %s", dlerror());
        return -1;
    }
    #define LOAD(name, preferred)                                                \
    do {                                                                     \
        const char *selected = preferred ? preferred : #name;                \
        g_##name = (pfn_##name)dlsym_prefer_v2(                              \
            g_libcuda, #name, preferred);                                    \
        if (g_##name == NULL) {                                              \
            CL_LOG("dlsym(%s/%s) failed: %s",                                \
                   #name, preferred ? preferred : "-", dlerror());            \
            goto err;                                                        \
        }                                                                    \
        CL_LOG("loaded %s for %s at %p",                                     \
               selected, #name, (void *)g_##name);                           \
    } while (0)

    LOAD(cuInit,                            NULL);
    LOAD(cuDriverGetVersion,                NULL);
    LOAD(cuDeviceGet,                       NULL);
    LOAD(cuDeviceGetCount,                  NULL);
    LOAD(cuDeviceGetName,                   NULL);
    LOAD(cuCtxCreate,                       "cuCtxCreate_v2");
    LOAD(cuCtxDestroy,                      "cuCtxDestroy_v2");
    LOAD(cuCtxSetCurrent,                   NULL);
    LOAD(cuCtxGetCurrent,                   NULL);
    LOAD(cuMemAlloc,                        "cuMemAlloc_v2");
    LOAD(cuMemFree,                         "cuMemFree_v2");
    LOAD(cuMemAllocHost,                    "cuMemAllocHost_v2");
    LOAD(cuMemFreeHost,                     NULL);
    LOAD(cuMemcpyDtoH,                      "cuMemcpyDtoH_v2");
    LOAD(cuMemcpyHtoD,                      "cuMemcpyHtoD_v2");
    LOAD(cuPointerSetAttribute,             NULL);
    LOAD(cuGetErrorName,                    NULL);
    LOAD(cuGetErrorString,                  NULL);
    /*
     * cuMemGetHandleForAddressRange 仅 CUDA Driver >= 12.2 提供, 早于此版本
     * 的驱动会 dlsym=NULL. 这条不是致命错: --gpu-mode=direct 会因此失败,
     * 但 proxy / peermem 仍然可用.
     */
    g_cuMemGetHandleForAddressRange =
        (pfn_cuMemGetHandleForAddressRange)dlsym2(g_libcuda,
            "cuMemGetHandleForAddressRange", NULL);
    if (g_cuMemGetHandleForAddressRange == NULL) {
        CL_LOG("note: cuMemGetHandleForAddressRange not present "
               "(CUDA < 12.2) -- --gpu-mode=direct will fail-fast");
    }

#undef LOAD

    g_loaded = 1;
    {
        int ver = 0;
        if (g_cuDriverGetVersion(&ver) == CUDA_SUCCESS) {
            CL_LOG("loaded libcuda.so.1, driver version=%d.%d",
                   ver / 1000, (ver % 1000) / 10);
        } else {
            CL_LOG("loaded libcuda.so.1 (driver version unknown)");
        }
    }
    return 0;

err:
    dlclose(g_libcuda);
    g_libcuda = NULL;
    return -1;
}

void perftest_cuda_loader_fini(void)
{
    perftest_cuda_ctx_destroy();
    if (g_libcuda != NULL) {
        dlclose(g_libcuda);
        g_libcuda = NULL;
    }
    g_loaded = 0;
    CL_LOG("ok");
}

int perftest_cuda_is_loaded(void)
{
    return g_loaded ? 1 : 0;
}

//DEBUG

static void dump_cuda_ptr(const char *name, void *p)
{
    typedef CUresult (*pfn_cuPointerGetAttribute_local)(
        void *data, int attribute, CUdeviceptr ptr);

    enum {
        PTR_ATTR_MEMORY_TYPE      = 2,
        PTR_ATTR_IS_MANAGED       = 8,
        PTR_ATTR_DEVICE_ORDINAL   = 9,
        PTR_ATTR_RANGE_START_ADDR = 11,
        PTR_ATTR_RANGE_SIZE       = 12
    };

    static pfn_cuPointerGetAttribute_local p_cuPointerGetAttribute = NULL;

    CUdeviceptr d;
    CUresult rc;
    int mem_type = 0;
    int dev = -1;
    unsigned int managed = 0;
    void *range_start = NULL;
    size_t range_size = 0;
    const char *type_str = "";

    if (!g_loaded || g_libcuda == NULL) {
        CL_LOG("%s=%p: loader not initialised", name, p);
        return;
    }

    if (p == NULL) {
        CL_LOG("%s=NULL", name);
        return;
    }

    if (p_cuPointerGetAttribute == NULL) {
        p_cuPointerGetAttribute =
            (pfn_cuPointerGetAttribute_local)dlsym2(
                g_libcuda, "cuPointerGetAttribute", NULL);

        if (p_cuPointerGetAttribute == NULL) {
            CL_LOG("%s=%p: cuPointerGetAttribute not available", name, p);
            return;
        }
    }

    d = (CUdeviceptr)(uintptr_t)p;

    rc = p_cuPointerGetAttribute(&mem_type, PTR_ATTR_MEMORY_TYPE, d);
    if (rc != CUDA_SUCCESS) {
        CL_LOG("%s=%p MEMORY_TYPE failed rc=%d", name, p, (int)rc);
        return;
    }

    rc = p_cuPointerGetAttribute(&dev, PTR_ATTR_DEVICE_ORDINAL, d);
    if (rc != CUDA_SUCCESS) {
        CL_LOG("%s=%p DEVICE_ORDINAL failed rc=%d", name, p, (int)rc);
    }

    rc = p_cuPointerGetAttribute(&managed, PTR_ATTR_IS_MANAGED, d);
    if (rc != CUDA_SUCCESS) {
        CL_LOG("%s=%p IS_MANAGED failed rc=%d", name, p, (int)rc);
    }

    rc = p_cuPointerGetAttribute(&range_start, PTR_ATTR_RANGE_START_ADDR, d);
    if (rc != CUDA_SUCCESS) {
        CL_LOG("%s=%p RANGE_START failed rc=%d", name, p, (int)rc);
    }

    rc = p_cuPointerGetAttribute(&range_size, PTR_ATTR_RANGE_SIZE, d);
    if (rc != CUDA_SUCCESS) {
        CL_LOG("%s=%p RANGE_SIZE failed rc=%d", name, p, (int)rc);
    }

    if (mem_type == 1) {
        type_str = "HOST";
    } else if (mem_type == 2) {
        type_str = "DEVICE";
    } else if (mem_type == 4) {
        type_str = "UNIFIED";
    } else {
        type_str = "UNKNOWN";
    }

    CL_LOG("%s=%p mem_type=%d(%s) dev=%d managed=%u range_start=%p range_size=%zu",
           name, p, mem_type, type_str, dev, managed, range_start, range_size);
}



/* ---- context ---- */
int perftest_cuda_ctx_create(int dev_id)
{
    CUresult rc;
    CUdevice cu_dev;
    int devcnt = 0;
    char name[128];

    if (!g_loaded) {
        CL_LOG("FAIL: loader not initialised");
        return -1;
    }
    if (g_ctx_owned) {
        CL_LOG("ctx already created (id=%d), reusing", dev_id);
        return 0;
    }

    rc = g_cuInit(0);
    if (rc != CUDA_SUCCESS) { CL_LOG("cuInit -> %d", (int)rc); return -1; }

    rc = g_cuDeviceGetCount(&devcnt);
    if (rc != CUDA_SUCCESS || devcnt <= 0) {
        CL_LOG("cuDeviceGetCount -> %d cnt=%d", (int)rc, devcnt); return -1;
    }
    if (dev_id < 0 || dev_id >= devcnt) {
        CL_LOG("dev_id=%d out of range [0,%d)", dev_id, devcnt); return -1;
    }
    rc = g_cuDeviceGet(&cu_dev, dev_id);
    if (rc != CUDA_SUCCESS) { CL_LOG("cuDeviceGet -> %d", (int)rc); return -1; }

    name[0] = '\0';
    g_cuDeviceGetName(name, sizeof(name), cu_dev);

    rc = g_cuCtxCreate(&g_ctx, CU_CTX_MAP_HOST, cu_dev);
    if (rc != CUDA_SUCCESS) { CL_LOG("cuCtxCreate -> %d", (int)rc); return -1; }
    rc = g_cuCtxSetCurrent(g_ctx);
    if (rc != CUDA_SUCCESS) { CL_LOG("cuCtxSetCurrent -> %d", (int)rc); return -1; }

    g_ctx_owned = 1;
    CL_LOG("ok: dev_id=%d name=\"%s\"", dev_id, name);
    return 0;
}

void perftest_cuda_ctx_destroy(void)
{
    if (g_ctx_owned && g_ctx != NULL && g_cuCtxDestroy != NULL) {
        g_cuCtxDestroy(g_ctx);
        CL_LOG("ok");
    }
    g_ctx       = NULL;
    g_ctx_owned = 0;
}

/* ---- memory ---- */
int perftest_cuda_mem_alloc(size_t size, void **out_dptr)
{
    CUdeviceptr d = 0;
    CUresult    rc;

    if (!g_loaded || out_dptr == NULL || !g_ctx) {
        CL_LOG("invalid state: loaded=%d out=%p ctx=%p",
               g_loaded, (void *)out_dptr, g_ctx);
        return -1;
    }

    /*
     * FIX: CUDA context 是 per-thread 状态 (current context), 不是
     * per-process 的。g_ctx 这个 CUcontext 对象本身在哪个线程创建都
     * 一样, 但"哪个 context 对当前线程生效"必须每次用之前显式
     * cuCtxSetCurrent() 一遍——如果调用这个函数的线程跟当初
     * perftest_cuda_ctx_create() 的线程不是同一个 (或者中间有别的
     * CUDA 调用换过 current context), 这里不重新 set 的话,
     * cuMemAlloc 本身可能就在错误的 (甚至没有) context 下执行。
     */
    rc = g_cuCtxSetCurrent(g_ctx);
    if (rc != CUDA_SUCCESS) {
        cl_log_cuda_error("cuCtxSetCurrent", rc);
        return -1;
    }

    rc = g_cuMemAlloc(&d, size);
    if (rc != CUDA_SUCCESS) {
        cl_log_cuda_error("cuMemAlloc", rc);
        return -1;
    }
    *out_dptr = (void *)(uintptr_t)d;
    CL_LOG("ok: ctx=%p dptr=%p cuda_va=0x%llx size=%zu",
           g_ctx, *out_dptr, (unsigned long long)d, size);
    return 0;
}

int perftest_cuda_mem_free(void *dptr)
{
    CUresult rc;
    if (!g_loaded) { return 0; }
    if (dptr == NULL) { return 0; }
    rc = g_cuMemFree((CUdeviceptr)(uintptr_t)dptr);
    if (rc != CUDA_SUCCESS) {
        CL_LOG("cuMemFree(%p) -> %d", dptr, (int)rc); return -1;
    }
    return 0;
}

int perftest_cuda_set_sync_memops(void *dptr)
{
    unsigned int flag = 1;
    CUdeviceptr  cuda_ptr;
    CUcontext    current_ctx = NULL;
    CUresult     rc;

    if (!g_loaded || !g_ctx || !g_cuPointerSetAttribute || !dptr) {
        CL_LOG("invalid state: loaded=%d ctx=%p fn=%p dptr=%p",
               g_loaded, g_ctx, (void *)g_cuPointerSetAttribute, dptr);
        return -1;
    }

    cuda_ptr = (CUdeviceptr)(uintptr_t)dptr;

    /*
     * FIX: 同 perftest_cuda_mem_alloc() —— current context 是 per-thread
     * 状态, 调用这个函数的线程未必就是当初创建/持有 g_ctx 的那个线程
     * (或者线程没变, 但期间别的 CUDA 调用把 current context 换掉了)。
     * 官方文档写得很明确: 如果当前 context 访问不到这个 ptr,
     * cuPointerSetAttribute 就会返回 CUDA_ERROR_INVALID_VALUE —— 这正是
     * 之前反复看到 "-> 1" 最可能的原因, 不是地址/长度/attribute 编号错。
     */
    rc = g_cuCtxSetCurrent(g_ctx);
    if (rc != CUDA_SUCCESS) {
        cl_log_cuda_error("cuCtxSetCurrent", rc);
        return -1;
    }

    /*
     * 显式回读一次, 而不是"set 成功就默认它生效了"——两者理论上应该一致,
     * 但既然上一轮已经在这个调用上栽了一次, 干脆多这一步交叉验证,
     * 免得又靠猜。
     */
    if (g_cuCtxGetCurrent != NULL) {
        rc = g_cuCtxGetCurrent(&current_ctx);
        if (rc != CUDA_SUCCESS) {
            cl_log_cuda_error("cuCtxGetCurrent", rc);
            return -1;
        }
        if (current_ctx != g_ctx) {
            CL_LOG("context mismatch after cuCtxSetCurrent: expected=%p current=%p",
                   g_ctx, current_ctx);
            return -1;
        }
    }

    CL_LOG("cuPointerSetAttribute enter: ctx=%p dptr=%p cuda_ptr=0x%llx "
           "flag=%u attr=%d fn=%p",
           current_ctx, dptr, (unsigned long long)cuda_ptr, flag,
           (int)CU_POINTER_ATTRIBUTE_SYNC_MEMOPS, (void *)g_cuPointerSetAttribute);

    rc = g_cuPointerSetAttribute(&flag, CU_POINTER_ATTRIBUTE_SYNC_MEMOPS, cuda_ptr);
    if (rc != CUDA_SUCCESS) {
        cl_log_cuda_error("cuPointerSetAttribute(SYNC_MEMOPS)", rc);
        return -1;
    }

    CL_LOG("ok: dptr=%p SYNC_MEMOPS=1 (required before handing this ptr to "
           "nvidia_p2p_get_pages(), otherwise kernel-side pin fails -EINVAL)",
           dptr);
    return 0;
}

int perftest_cuda_mem_alloc_host(size_t size, void **out_hptr)
{
    CUresult rc;
    if (!g_loaded || out_hptr == NULL) { CL_LOG("invalid arg"); return -1; }
    rc = g_cuMemAllocHost(out_hptr, size);
    if (rc != CUDA_SUCCESS) {
        CL_LOG("cuMemAllocHost(%zu) -> %d", size, (int)rc); return -1;
    }
    CL_LOG("ok: hptr=%p size=%zu (pinned)", *out_hptr, size);
    return 0;
}

int perftest_cuda_mem_free_host(void *hptr)
{
    CUresult rc;
    if (!g_loaded) { return 0; }
    if (hptr == NULL) { return 0; }
    rc = g_cuMemFreeHost(hptr);
    if (rc != CUDA_SUCCESS) {
        CL_LOG("cuMemFreeHost(%p) -> %d", hptr, (int)rc); return -1;
    }
    return 0;
}

/* ---- D2H / H2D ---- */
int perftest_cuda_memcpy_d2h(void *host, void *dev, size_t n)
{
    CUresult rc;
    if (!g_loaded) { CL_LOG("loader not initialised"); return -1; }
    if (perftest_cuda_make_ctx_current() != 0)    return -1; //check if current thread has the context

    rc = g_cuMemcpyDtoH(host, (CUdeviceptr)(uintptr_t)dev, n);
    if (rc != CUDA_SUCCESS) {
        CL_LOG("cuMemcpyDtoH -> %d (host=%p dev=%p n=%zu)",
               (int)rc, host, dev, n);
        return -1;
    }
    //printf("ACTUALLY GO TO CUDA\n");
    return 0;
}

int perftest_cuda_memcpy_h2d(void *dev, const void *host, size_t n)
{
    CUresult rc;
    if (!g_loaded) { CL_LOG("loader not initialised"); return -1; }
    if (perftest_cuda_make_ctx_current() != 0)    return -1; //check if current thread has the context
    rc = g_cuMemcpyHtoD((CUdeviceptr)(uintptr_t)dev, host, n);
    if (rc != CUDA_SUCCESS) {
        CL_LOG("cuMemcpyHtoD -> %d (dev=%p host=%p n=%zu)",
               (int)rc, dev, host, n);
        return -1;
    }
    return 0;
}



/* ---- dma-buf ---- */
int perftest_cuda_get_dmabuf_fd(void *dptr, size_t size,
                                int *out_fd, uint64_t *out_offset)
{
    int      fd = -1;
    CUresult rc;
    if (perftest_cuda_make_ctx_current() != 0)
    return -1;



    dump_cuda_ptr("dmabuf dptr", dptr);

    if (!g_loaded || out_fd == NULL || out_offset == NULL) {
        CL_LOG("invalid arg"); return -1;
    }
    if (g_cuMemGetHandleForAddressRange == NULL) {
        CL_LOG("FAIL: cuMemGetHandleForAddressRange not available "
               "(needs CUDA Driver >= 12.2)");
        return -1;
    }
    /*
     * 真实现里要先对齐到 host page size 才行, 这里照搬 linux-rdma cuda_memory.c
     * 的做法: dptr & ~(pagesz-1), 然后偏移 = dptr - aligned.
     */
    {
        size_t      pagesz  = (size_t)sysconf(_SC_PAGESIZE);
        CUdeviceptr d       = (CUdeviceptr)(uintptr_t)dptr;
        CUdeviceptr aligned = d & ~((CUdeviceptr)pagesz - 1);
        uint64_t    offset  = (uint64_t)(d - aligned);
        size_t      asize   = (size + (size_t)offset + pagesz - 1) & ~(pagesz - 1);

        rc = g_cuMemGetHandleForAddressRange(
                &fd, aligned, asize,
                CU_MEM_RANGE_HANDLE_TYPE_DMA_BUF_FD, 0);
        if (rc != CUDA_SUCCESS) {
            CL_LOG("cuMemGetHandleForAddressRange -> %d", (int)rc);
            return -1;
        }
        *out_fd     = fd;
        *out_offset = offset;
        CL_LOG("ok: dptr=%p size=%zu -> fd=%d offset=%lu",
               dptr, size, fd, (unsigned long)offset);
    }
    return 0;
}

