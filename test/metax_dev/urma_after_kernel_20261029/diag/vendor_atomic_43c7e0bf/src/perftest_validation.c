/*
 * SPDX-License-Identifier: MIT
 * Copyright (c) Huawei Technologies Co., Ltd. 2024-2026. All rights reserved.
 *
 * xorshift32 fill/check + optional CUDA H2D/D2H for device buffers.
 */

#ifndef _GNU_SOURCE
#define _GNU_SOURCE
#endif

#include <stddef.h>
#include <stdint.h>
#include <stdio.h>
#include <stdlib.h>
#include <string.h>

#include "perftest_cuda_loader.h"
#include "perftest_metax_loader.h"
#include "perftest_validation.h"

int perftest_validation_memcpy_h2d(void *dev, const void *host, size_t n)
{
    if (perftest_metax_is_loaded()) {
        return perftest_metax_memcpy_h2d(dev, host, n);
    }
    if (perftest_cuda_is_loaded()) {
        return perftest_cuda_memcpy_h2d(dev, host, n);
    }
    return -1;
}

int perftest_validation_memcpy_d2h(void *host, void *dev, size_t n)
{
    if (perftest_metax_is_loaded()) {
        return perftest_metax_memcpy_d2h(host, dev, n);
    }
    if (perftest_cuda_is_loaded()) {
        return perftest_cuda_memcpy_d2h(host, dev, n);
    }
    return -1;
}

#define V_LOG(fmt, ...) \
    fprintf(stderr, "[validation] %s: " fmt "\n", __func__, ##__VA_ARGS__)

/*
 * 简单 xorshift32: 与 linux-rdma validation_common.c 相同的"廉价 PRNG"风格.
 * 32-bit 周期 2^32-1, 测试数据足够; 真要做密码学强度的校验, 后续可以替换.
 */
static inline uint32_t xs32_step(uint32_t *s)
{
    uint32_t x = *s ? *s : 0xA5A5A5A5u;
    x ^= x << 13;
    x ^= x >> 17;
    x ^= x << 5;
    *s = x;
    return x;
}

void perftest_validation_fill_host(void *buf, size_t size, uint32_t seed)
{
    uint8_t *p = (uint8_t *)buf;
    uint32_t s = seed;
    size_t   i;

    if (buf == NULL || size == 0) {
        V_LOG("invalid arg buf=%p size=%zu", buf, size);
        return;
    }
    for (i = 0; i < size; i++) {
        p[i] = (uint8_t)(xs32_step(&s) & 0xFFu);
    }
    V_LOG("filled %zu bytes seed=0x%x", size, seed);
}

size_t perftest_validation_check_host(const void *buf, size_t size, uint32_t seed)
{
    const uint8_t *p = (const uint8_t *)buf;
    uint32_t s = seed;
    size_t   i;
    size_t   bad = 0;

    if (buf == NULL || size == 0) {
        V_LOG("invalid arg buf=%p size=%zu", buf, size);
        return size; /* treat as 'all bad' */
    }
    for (i = 0; i < size; i++) {
        uint8_t want = (uint8_t)(xs32_step(&s) & 0xFFu);
        if (p[i] != want) {
            bad++;
        }
    }
    V_LOG("checked %zu bytes seed=0x%x mismatched=%zu", size, seed, bad);
    return bad;
}

/* ---- GPU variants ----------------------------------------------------- */
/*
 * 共用一个 host 临时 buffer, 大小受 size 限制; 真实部署里 size 可能很大
 * (BW 测试 8MB), 直接 malloc 即可. 校验路径不在 hot loop, 一次性开销.
 */

int perftest_validation_fill_gpu(void *dptr, size_t size, uint32_t seed)
{
    uint8_t *tmp;
    int      rc;

    if (!perftest_metax_is_loaded() && !perftest_cuda_is_loaded()) {
        V_LOG("FAIL: no MetaX/CUDA loader initialised");
        return -1;
    }
    if (dptr == NULL || size == 0) {
        V_LOG("invalid arg dptr=%p size=%zu", dptr, size);
        return -1;
    }
    /* peermem 热路径外: H2D 前必须 current CUcontext. */
    if (perftest_cuda_is_loaded() && perftest_cuda_set_current_ctx() != 0) {
        V_LOG("FAIL: cuCtxSetCurrent");
        return -1;
    }
    tmp = (uint8_t *)malloc(size);
    if (tmp == NULL) {
        V_LOG("ENOMEM tmp=%zu", size);
        return -1;
    }
    perftest_validation_fill_host(tmp, size, seed);
    rc = perftest_validation_memcpy_h2d(dptr, tmp, size);
    free(tmp);
    if (rc != 0) {
        V_LOG("FAIL: memcpy H2D rc=%d", rc);
        return -1;
    }
    V_LOG("ok: H2D dptr=%p size=%zu seed=0x%x", dptr, size, seed);
    return 0;
}

long perftest_validation_check_gpu(void *dptr, size_t size, uint32_t seed)
{
    uint8_t *tmp;
    size_t   bad;
    int      rc;

    if (!perftest_metax_is_loaded() && !perftest_cuda_is_loaded()) {
        V_LOG("FAIL: no MetaX/CUDA loader initialised");
        return -1;
    }
    if (dptr == NULL || size == 0) {
        V_LOG("invalid arg dptr=%p size=%zu", dptr, size);
        return -1;
    }
    if (perftest_cuda_is_loaded() && perftest_cuda_set_current_ctx() != 0) {
        V_LOG("FAIL: cuCtxSetCurrent");
        return -1;
    }
    tmp = (uint8_t *)malloc(size);
    if (tmp == NULL) {
        V_LOG("ENOMEM tmp=%zu", size);
        return -1;
    }
    rc = perftest_validation_memcpy_d2h(tmp, dptr, size);
    if (rc != 0) {
        free(tmp);
        V_LOG("FAIL: memcpy D2H rc=%d", rc);
        return -1;
    }
    bad = perftest_validation_check_host(tmp, size, seed);
    free(tmp);
    V_LOG("ok: D2H dptr=%p size=%zu seed=0x%x mismatched=%zu",
          dptr, size, seed, bad);
    return (long)bad;
}

int perftest_validation_fill(void *addr, size_t size, uint32_t seed, bool is_device_va)
{
    if (addr == NULL || size == 0) {
        return -1;
    }
    if (is_device_va) {
        return perftest_validation_fill_gpu(addr, size, seed);
    }
    perftest_validation_fill_host(addr, size, seed);
    return 0;
}

long perftest_validation_check(void *addr, size_t size, uint32_t seed, bool is_device_va)
{
    if (addr == NULL || size == 0) {
        return -1;
    }
    if (is_device_va) {
        return perftest_validation_check_gpu(addr, size, seed);
    }
    return (long)perftest_validation_check_host(addr, size, seed);
}

/* ---- Asymmetric GPU eye-verify (host plant / GPU D2H dump) ------------- */

const char *perftest_gdr_eye_magic(void)
{
    return PERFTEST_GDR_EYE_MAGIC;
}

size_t perftest_gdr_eye_min_size(void)
{
    /* payload 最后 1 字节是 write_lat flag, magic 占前面. */
    return strlen(PERFTEST_GDR_EYE_MAGIC) + 1u;
}

static int gdr_eye_fill_send(void *send_base, uint32_t size)
{
    size_t magic_len = strlen(PERFTEST_GDR_EYE_MAGIC);
    size_t copy_len;

    if (send_base == NULL) {
        return -1;
    }
    if ((size_t)size < perftest_gdr_eye_min_size()) {
        V_LOG("SKIP plant: size=%u < min=%zu (use -s >= %zu)",
              size, perftest_gdr_eye_min_size(), perftest_gdr_eye_min_size());
        return -1;
    }
    /* 不覆盖 size-1 的 flag 字节. */
    copy_len = magic_len;
    if (copy_len > (size_t)size - 1u) {
        copy_len = (size_t)size - 1u;
    }
    memcpy(send_base, PERFTEST_GDR_EYE_MAGIC, copy_len);
    if (copy_len < (size_t)size - 1u) {
        memset((char *)send_base + copy_len, 0, (size_t)size - 1u - copy_len);
    }
    return 0;
}

int perftest_gdr_eye_plant_host(void *send_base, uint32_t size)
{
    if (gdr_eye_fill_send(send_base, size) != 0) {
        return -1;
    }
    V_LOG("host planted magic=\"%s\" at send[0] (size=%u, flag@%u untouched)",
          PERFTEST_GDR_EYE_MAGIC, size, size - 1u);
    (void)fprintf(stderr,
        "[gdr-verify] HOST planted \"%s\" at send[0] (payload size=%u)\n",
        PERFTEST_GDR_EYE_MAGIC, size);
    return 0;
}

int perftest_gdr_eye_plant(void *send_base, uint32_t size, bool is_device_va)
{
    char *tmp;
    int rc;

    if (!is_device_va) {
        return perftest_gdr_eye_plant_host(send_base, size);
    }
    if (send_base == NULL) {
        return -1;
    }
    if (!perftest_metax_is_loaded() && !perftest_cuda_is_loaded()) {
        V_LOG("FAIL: no MetaX/CUDA loader initialised");
        return -1;
    }
    if (perftest_cuda_is_loaded() && perftest_cuda_set_current_ctx() != 0) {
        V_LOG("FAIL: cuCtxSetCurrent");
        return -1;
    }
    tmp = (char *)calloc(1, size);
    if (tmp == NULL) {
        return -1;
    }
    if (gdr_eye_fill_send(tmp, size) != 0) {
        free(tmp);
        return -1;
    }
    rc = perftest_validation_memcpy_h2d(send_base, tmp, size);
    free(tmp);
    if (rc != 0) {
        V_LOG("FAIL: memcpy H2D plant rc=%d", rc);
        return -1;
    }
    (void)fprintf(stderr,
        "[gdr-verify] GPU planted \"%s\" at send[0] (payload size=%u)\n",
        PERFTEST_GDR_EYE_MAGIC, size);
    return 0;
}

int perftest_gdr_eye_init_gpu_recv(void *recv_dptr, uint32_t size)
{
    size_t mark_len = strlen(PERFTEST_GDR_EYE_INIT_MARK);
    size_t copy_len;
    char  *tmp;
    int    rc;

    if (recv_dptr == NULL || size == 0) {
        return -1;
    }
    if (!perftest_metax_is_loaded() && !perftest_cuda_is_loaded()) {
        V_LOG("FAIL: no MetaX/CUDA loader initialised");
        return -1;
    }
    if (perftest_cuda_is_loaded() && perftest_cuda_set_current_ctx() != 0) {
        return -1;
    }
    tmp = (char *)calloc(1, size);
    if (tmp == NULL) {
        return -1;
    }
    copy_len = mark_len;
    if (copy_len > (size_t)size - (size > 1 ? 1u : 0u)) {
        copy_len = size > 1 ? (size_t)size - 1u : (size_t)size;
    }
    if (copy_len > 0) {
        memcpy(tmp, PERFTEST_GDR_EYE_INIT_MARK, copy_len);
    }
    rc = perftest_validation_memcpy_h2d(recv_dptr, tmp, size);
    free(tmp);
    if (rc != 0) {
        V_LOG("FAIL: H2D init mark rc=%d", rc);
        return -1;
    }
    (void)fprintf(stderr,
        "[gdr-verify] GPU recv prefilled with \"%s\" (expect overwrite by inbound WRITE)\n",
        PERFTEST_GDR_EYE_INIT_MARK);
    return 0;
}

int perftest_gdr_eye_dump_gpu_recv(void *recv_dptr, uint32_t size)
{
    size_t dump_len;
    size_t i;
    char  *tmp;
    int    rc;

    if (recv_dptr == NULL || size == 0) {
        return -1;
    }
    if (!perftest_metax_is_loaded() && !perftest_cuda_is_loaded()) {
        V_LOG("FAIL: no MetaX/CUDA loader initialised");
        return -1;
    }
    if (perftest_cuda_is_loaded() && perftest_cuda_set_current_ctx() != 0) {
        return -1;
    }
    /* 打印 payload 前缀, 不含最后 flag 字节; 至少打到 magic 长度. */
    dump_len = (size_t)size > 1u ? (size_t)size - 1u : (size_t)size;
    if (dump_len > 64u) {
        dump_len = 64u;
    }
    tmp = (char *)calloc(1, dump_len + 1u);
    if (tmp == NULL) {
        return -1;
    }
    rc = perftest_validation_memcpy_d2h(tmp, recv_dptr, dump_len);
    if (rc != 0) {
        free(tmp);
        V_LOG("FAIL: D2H dump rc=%d", rc);
        return -1;
    }
    tmp[dump_len] = '\0';
    (void)fprintf(stderr,
        "[gdr-verify] GPU D2H recv[0..%zu): ascii=\"%s\"\n", dump_len, tmp);
    (void)fprintf(stderr, "[gdr-verify] GPU D2H recv hex:");
    for (i = 0; i < dump_len; i++) {
        (void)fprintf(stderr, " %02x", (unsigned char)tmp[i]);
    }
    (void)fprintf(stderr, "\n");
    if (strncmp(tmp, PERFTEST_GDR_EYE_MAGIC, strlen(PERFTEST_GDR_EYE_MAGIC)) == 0) {
        (void)fprintf(stderr,
            "[gdr-verify] PASS: saw magic \"%s\" in GPU memory (host->GPU write OK)\n",
            PERFTEST_GDR_EYE_MAGIC);
    } else if (strncmp(tmp, PERFTEST_GDR_EYE_INIT_MARK,
                       strlen(PERFTEST_GDR_EYE_INIT_MARK)) == 0) {
        (void)fprintf(stderr,
            "[gdr-verify] FAIL: still INIT mark — host write did not land on GPU\n");
    } else {
        (void)fprintf(stderr,
            "[gdr-verify] CHECK: ascii != magic/init; inspect hex above "
            "(need host -s >= %zu and host planted magic)\n",
            perftest_gdr_eye_min_size());
    }
    free(tmp);
    return 0;
}
