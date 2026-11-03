/*
 * SPDX-License-Identifier: MIT
 * Copyright (c) Huawei Technologies Co., Ltd. 2024-2026. All rights reserved.
 *
 * dlopen/dlsym wrappers for MetaX MACA runtime (no mc_runtime.h at build).
 *
 * Symbols match the published MACA runtime ABI (CUDA-runtime shaped):
 *   mcSetDevice / mcMalloc / mcFree / mcMemcpy
 *
 * Search order: PERFTEST_METAX_LIB, then $MACA_PATH/{lib,lib64},
 * /opt/maca, /usr/local/maca, then the default linker path.
 */

#ifndef _GNU_SOURCE
#define _GNU_SOURCE
#endif

#include <dlfcn.h>
#include <errno.h>
#include <stdio.h>
#include <stdlib.h>
#include <string.h>
#include <unistd.h>

#include "perftest_metax_loader.h"

#define MX_LOG(fmt, ...) \
    fprintf(stderr, "[metax-loader] %s: " fmt "\n", __func__, ##__VA_ARGS__)

#define MC_SUCCESS                 0
#define MC_MEMCPY_HOST_TO_DEVICE   1
#define MC_MEMCPY_DEVICE_TO_HOST   2

typedef int mcError_t;

typedef mcError_t (*pfn_mcSetDevice)(int device);
typedef mcError_t (*pfn_mcGetDeviceCount)(int *count);
typedef mcError_t (*pfn_mcMalloc)(void **devPtr, size_t size);
typedef mcError_t (*pfn_mcFree)(void *devPtr);
typedef mcError_t (*pfn_mcMemcpy)(void *dst, const void *src, size_t count,
                                  int kind);

static int               g_loaded;
static int               g_device_set;
static void             *g_lib;
static pfn_mcSetDevice   g_mcSetDevice;
static pfn_mcGetDeviceCount g_mcGetDeviceCount;
static pfn_mcMalloc      g_mcMalloc;
static pfn_mcFree        g_mcFree;
static pfn_mcMemcpy      g_mcMemcpy;

static int path_ok(const char *p)
{
    return p != NULL && p[0] != '\0' && access(p, R_OK) == 0;
}

static void *try_dlopen(const char *path)
{
    void *h;

    if (!path_ok(path)) {
        return NULL;
    }
    h = dlopen(path, RTLD_LAZY);
    if (h == NULL) {
        MX_LOG("dlopen %s failed: %s", path, dlerror());
    }
    return h;
}

static void *open_mcruntime(void)
{
    const char *env = getenv("PERFTEST_METAX_LIB");
    const char *home = getenv("MACA_PATH");
    char buf[512];
    void *h;
    size_t i;
    static const char *const names[] = {
        "libmcruntime.so",
        "libmcruntime.so.1",
        "libmacart.so",
        "libmaca.so",
    };
    static const char *const roots[] = {
        "/opt/maca",
        "/usr/local/maca",
    };

    if (env != NULL && env[0] != '\0') {
        h = try_dlopen(env);
        if (h != NULL) {
            MX_LOG("loaded PERFTEST_METAX_LIB=%s", env);
            return h;
        }
    }

    if (home != NULL && home[0] != '\0') {
        for (i = 0; i < sizeof(names) / sizeof(names[0]); i++) {
            snprintf(buf, sizeof(buf), "%s/lib/%s", home, names[i]);
            h = try_dlopen(buf);
            if (h != NULL) {
                return h;
            }
            snprintf(buf, sizeof(buf), "%s/lib64/%s", home, names[i]);
            h = try_dlopen(buf);
            if (h != NULL) {
                return h;
            }
        }
    }

    for (i = 0; i < sizeof(roots) / sizeof(roots[0]); i++) {
        size_t j;

        for (j = 0; j < sizeof(names) / sizeof(names[0]); j++) {
            snprintf(buf, sizeof(buf), "%s/lib/%s", roots[i], names[j]);
            h = try_dlopen(buf);
            if (h != NULL) {
                return h;
            }
            snprintf(buf, sizeof(buf), "%s/lib64/%s", roots[i], names[j]);
            h = try_dlopen(buf);
            if (h != NULL) {
                return h;
            }
        }
    }

    for (i = 0; i < sizeof(names) / sizeof(names[0]); i++) {
        h = dlopen(names[i], RTLD_LAZY);
        if (h != NULL) {
            MX_LOG("loaded %s from default linker path", names[i]);
            return h;
        }
    }
    return NULL;
}

static void *must_sym(void *h, const char *name)
{
    void *s = dlsym(h, name);

    if (s == NULL) {
        MX_LOG("missing symbol %s: %s", name, dlerror());
    }
    return s;
}

int perftest_metax_loader_init(void)
{
    if (g_loaded) {
        return 0;
    }

    g_lib = open_mcruntime();
    if (g_lib == NULL) {
        MX_LOG("FAIL: libmcruntime not found "
               "(install MACA, or export MACA_PATH=/opt/maca, "
               "or PERFTEST_METAX_LIB=/path/to/libmcruntime.so)");
        return -1;
    }

    g_mcSetDevice = must_sym(g_lib, "mcSetDevice");
    g_mcMalloc = must_sym(g_lib, "mcMalloc");
    g_mcFree = must_sym(g_lib, "mcFree");
    g_mcMemcpy = must_sym(g_lib, "mcMemcpy");
    g_mcGetDeviceCount = dlsym(g_lib, "mcGetDeviceCount");

    if (g_mcSetDevice == NULL || g_mcMalloc == NULL ||
        g_mcFree == NULL || g_mcMemcpy == NULL) {
        dlclose(g_lib);
        g_lib = NULL;
        return -1;
    }

    g_loaded = 1;
    MX_LOG("loaded MetaX MACA runtime");
    return 0;
}

void perftest_metax_loader_fini(void)
{
    if (g_lib != NULL) {
        dlclose(g_lib);
        g_lib = NULL;
    }
    g_loaded = 0;
    g_device_set = 0;
    g_mcSetDevice = NULL;
    g_mcGetDeviceCount = NULL;
    g_mcMalloc = NULL;
    g_mcFree = NULL;
    g_mcMemcpy = NULL;
}

int perftest_metax_is_loaded(void)
{
    return g_loaded;
}

int perftest_metax_set_device(int dev_id)
{
    mcError_t err;
    int count = 0;

    if (perftest_metax_loader_init() != 0) {
        return -1;
    }
    if (dev_id < 0) {
        dev_id = 0;
    }
    if (g_mcGetDeviceCount != NULL) {
        err = g_mcGetDeviceCount(&count);
        if (err != MC_SUCCESS || count <= 0) {
            MX_LOG("mcGetDeviceCount err=%d count=%d", (int)err, count);
            return -1;
        }
        if (dev_id >= count) {
            MX_LOG("dev_id=%d out of range [0,%d)", dev_id, count);
            return -1;
        }
    }
    err = g_mcSetDevice(dev_id);
    if (err != MC_SUCCESS) {
        MX_LOG("mcSetDevice(%d) err=%d", dev_id, (int)err);
        return -1;
    }
    g_device_set = 1;
    MX_LOG("ok: device %d", dev_id);
    return 0;
}

int perftest_metax_mem_alloc(size_t size, void **out_dptr)
{
    void *ptr = NULL;
    mcError_t err;

    if (!g_loaded || out_dptr == NULL || size == 0) {
        return -1;
    }
    if (!g_device_set && perftest_metax_set_device(0) != 0) {
        return -1;
    }
    err = g_mcMalloc(&ptr, size);
    if (err != MC_SUCCESS || ptr == NULL) {
        MX_LOG("mcMalloc(%zu) err=%d ptr=%p", size, (int)err, ptr);
        return -1;
    }
    *out_dptr = ptr;
    MX_LOG("ok: dptr=%p size=%zu", ptr, size);
    return 0;
}

int perftest_metax_mem_free(void *dptr)
{
    mcError_t err;

    if (!g_loaded || dptr == NULL) {
        return 0;
    }
    err = g_mcFree(dptr);
    if (err != MC_SUCCESS) {
        MX_LOG("mcFree(%p) err=%d", dptr, (int)err);
        return -1;
    }
    return 0;
}

int perftest_metax_memcpy_d2h(void *host, void *dev, size_t n)
{
    mcError_t err;

    if (!g_loaded || host == NULL || dev == NULL) {
        return -1;
    }
    err = g_mcMemcpy(host, dev, n, MC_MEMCPY_DEVICE_TO_HOST);
    if (err != MC_SUCCESS) {
        MX_LOG("mcMemcpy D2H err=%d n=%zu", (int)err, n);
        return -1;
    }
    return 0;
}

int perftest_metax_memcpy_h2d(void *dev, const void *host, size_t n)
{
    mcError_t err;

    if (!g_loaded || host == NULL || dev == NULL) {
        return -1;
    }
    err = g_mcMemcpy(dev, host, n, MC_MEMCPY_HOST_TO_DEVICE);
    if (err != MC_SUCCESS) {
        MX_LOG("mcMemcpy H2D err=%d n=%zu", (int)err, n);
        return -1;
    }
    return 0;
}
