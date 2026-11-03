/*
 * SPDX-License-Identifier: MIT
 * Copyright (c) Huawei Technologies Co., Ltd. 2024-2026. All rights reserved.
 *
 * Legacy stub / test helper: perftest_gpu_alloc_test.c
 */
#include <stdio.h>
#include <stdlib.h>
#include <string.h>

#include "perftest_gpu_alloc.h"
#include "perftest_validation.h"

/* 简洁 print 宏, 与被测代码一致用 __func__. */
#define T_LOG(fmt, ...) \
    fprintf(stderr, "[gpu-alloc-test] %s: " fmt "\n", __func__, ##__VA_ARGS__)

#define EXPECT(cond, msg) do {                                          \
    if (!(cond)) {                                                      \
        fprintf(stderr,                                                 \
            "[gpu-alloc-test] FAIL %s:%d  (%s)  %s\n",                  \
            __FILE__, __LINE__, #cond, msg);                            \
        failures++;                                                     \
    }                                                                   \
} while (0)

static int failures;

/*
 * 单条用例: 强制前 N 级失败, 期望最终落到 expected 策略.
 *   force_mask : 位 (1<<level) 置 1 表示让该级 try_alloc 强制失败
 *   expected   : 落地策略, 由 print 比对即可
 */
static void run_case(const char *label, unsigned force_mask,
                     perftest_gpu_alloc_strategy_t expected)
{
    perftest_gpu_buffer_t buf;
    int rc;

    T_LOG("--- case: %s  force_mask=0x%x  expect=%s ---",
          label, force_mask, perftest_gpu_alloc_strategy_name(expected));

    perftest_gpu_alloc_set_force_fail_mask(force_mask);
    rc = perftest_gpu_alloc_buffer(8192, 4096, &buf);

    EXPECT(rc == 0, "perftest_gpu_alloc_buffer should never fail (memalign is unconditional)");
    EXPECT(buf.strategy == expected, "landed strategy mismatches expected");
    EXPECT(buf.addr != NULL, "addr must be non-NULL");
    EXPECT(buf.size == 8192, "size must be carried through");

    /* 简单地写一字节, 证明返回的 addr 可写 (host 可见). */
    if (buf.addr) {
        ((unsigned char *)buf.addr)[0] = 0x7F;
        EXPECT(((unsigned char *)buf.addr)[0] == 0x7F, "addr should be writable");
    }

    perftest_gpu_free_buffer(&buf);
    EXPECT(buf.addr == NULL, "after free, struct should be zeroed");
    EXPECT(buf.strategy == PERFTEST_GPU_ALLOC_NONE, "strategy zeroed too");
}

int main(int argc, char **argv)
{
    (void)argc; (void)argv;
    T_LOG("starting minimal --gpu-mode dispatch test (libc only)");

    /*
     * 注意: 上一轮的"无条件 GDR->peermem->proxy->memalign"链路, 在本轮被
     * 重构成"按 --gpu-mode 选一档 + fallback memalign". 因此这里用
     * perftest_gpu_set_mode() 模拟 CLI 选择.
     */

    /*
     * mode=NONE (默认): 不进 GPU 链, 直接 memalign.
     */
    perftest_gpu_set_mode(PERFTEST_GPU_MODE_NONE);
    run_case("mode=NONE  -> memalign (default behaviour)",
             0,
             PERFTEST_GPU_ALLOC_MEMALIGN);

    /*
     * mode=PROXY 在无 libcuda 时若不开 sim 也是失败 -> fallback memalign.
     */
    perftest_gpu_set_mode(PERFTEST_GPU_MODE_PROXY);
    run_case("mode=PROXY without libcuda/sim -> memalign fallback",
             0,
             PERFTEST_GPU_ALLOC_MEMALIGN);

    /*
     * 开 sim, mode=PROXY 应当落 HOST_PROXY.
     */
    setenv("PERFTEST_GPU_ALLOC_ALLOW_HOST_PROXY_SIM", "1", 1);
    run_case("mode=PROXY with sim -> HOST_PROXY",
             0,
             PERFTEST_GPU_ALLOC_HOST_PROXY);
    EXPECT(perftest_gpu_proxy_stages() == true, "proxy stages on");

    perftest_gpu_set_mode(PERFTEST_GPU_MODE_PROXY_PURE);
    run_case("mode=PROXY_PURE with sim -> HOST_PROXY",
             0,
             PERFTEST_GPU_ALLOC_HOST_PROXY);
    EXPECT(perftest_gpu_proxy_stages() == false, "proxy_pure stages off");

    perftest_gpu_set_mode(PERFTEST_GPU_MODE_PROXY);
    /*
     * 强制 host-proxy 失败, 仍走 fallback memalign.
     */
    run_case("mode=PROXY + force HOST_PROXY fail -> memalign",
             1u << PERFTEST_GPU_ALLOC_HOST_PROXY,
             PERFTEST_GPU_ALLOC_MEMALIGN);

    /*
     * mode=PEERMEM 在 cloud agent 上无 cuda + 无 peermem 模块 -> memalign.
     */
    perftest_gpu_alloc_set_force_fail_mask(0);
    perftest_gpu_set_mode(PERFTEST_GPU_MODE_PEERMEM);
    run_case("mode=PEERMEM (no cuda) -> memalign fallback",
             0,
             PERFTEST_GPU_ALLOC_MEMALIGN);

    /*
     * mode=DIRECT 同上.
     */
    perftest_gpu_set_mode(PERFTEST_GPU_MODE_DIRECT);
    run_case("mode=DIRECT (no cuda) -> memalign fallback",
             0,
             PERFTEST_GPU_ALLOC_MEMALIGN);

    /*
     * mode=METAX without MACA runtime -> memalign.
     */
    perftest_gpu_set_mode(PERFTEST_GPU_MODE_METAX);
    run_case("mode=METAX (no libmcruntime) -> memalign fallback",
             0,
             PERFTEST_GPU_ALLOC_MEMALIGN);
    run_case("mode=METAX + force METAX fail -> memalign",
             1u << PERFTEST_GPU_ALLOC_METAX,
             PERFTEST_GPU_ALLOC_MEMALIGN);

    /*
     * 字符串解析 sanity check.
     */
    {
        perftest_gpu_mode_t m;
        EXPECT(perftest_gpu_mode_from_string("proxy",   &m) == 0 &&
               m == PERFTEST_GPU_MODE_PROXY, "from_string proxy");
        EXPECT(perftest_gpu_mode_from_string("proxy_pure", &m) == 0 &&
               m == PERFTEST_GPU_MODE_PROXY_PURE, "from_string proxy_pure");
        EXPECT(perftest_gpu_mode_from_string("peermem", &m) == 0 &&
               m == PERFTEST_GPU_MODE_PEERMEM, "from_string peermem");
        EXPECT(perftest_gpu_mode_from_string("direct",  &m) == 0 &&
               m == PERFTEST_GPU_MODE_DIRECT, "from_string direct");
        EXPECT(perftest_gpu_mode_from_string("metax",   &m) == 0 &&
               m == PERFTEST_GPU_MODE_METAX, "from_string metax");
        EXPECT(perftest_gpu_mode_from_string("none",    &m) == 0 &&
               m == PERFTEST_GPU_MODE_NONE, "from_string none");
        EXPECT(perftest_gpu_mode_from_string("bogus",   &m) != 0,
               "from_string bogus must fail");
        EXPECT(perftest_gpu_mode_from_string("muxi",    &m) != 0,
               "from_string muxi must fail");
        EXPECT(perftest_gpu_mode_from_string("mx",      &m) != 0,
               "from_string mx must fail");
        EXPECT(perftest_gpu_mode_from_string("maca",    &m) != 0,
               "from_string maca must fail");
    }

    /*
     * gdr-verify plant: magic at send[0], last-byte flag left alone.
     */
    {
        char buf[64];
        const char *magic = perftest_gdr_eye_magic();
        size_t magic_len = strlen(magic);

        memset(buf, 0xaa, sizeof(buf));
        EXPECT(perftest_gdr_eye_plant_host(buf, (uint32_t)sizeof(buf)) == 0,
               "plant_host");
        EXPECT(memcmp(buf, magic, magic_len) == 0, "magic at send[0]");
        EXPECT((unsigned char)buf[sizeof(buf) - 1u] == 0xaau,
               "flag byte size-1 untouched");
        EXPECT(perftest_gdr_eye_min_size() == magic_len + 1u, "min_size = magic+flag");
        EXPECT(perftest_gdr_eye_plant(buf, (uint32_t)sizeof(buf), false) == 0,
               "plant host via is_device_va=false");
        EXPECT(perftest_gdr_eye_plant(buf, (uint32_t)sizeof(buf), true) != 0,
               "plant GPU without CUDA/MetaX must fail");
        EXPECT(perftest_validation_memcpy_h2d(buf, buf, 1) != 0,
               "memcpy without GPU loader must fail");
    }

    if (failures == 0) {
        fprintf(stderr, "\n[gpu-alloc-test] ALL CASES PASSED\n");
        return 0;
    }
    fprintf(stderr, "\n[gpu-alloc-test] %d FAILURE(S)\n", failures);
    return 1;
}
