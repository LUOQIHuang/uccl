# URMA Mode Parity v2_03 — 对齐修复验证通过

- **测试**: `test/urma_mode_parity` v2_03（按 `ATOMIC_ALIGNMENT_FIX.zh-CN.md` 流程）
- **时间**: 2026-11-05 04:00–04:20
- **结论**: ✅ **Normal + Low-Latency 双模式双机全部通过**。对齐修复（`90f1c905`）生效，GPU 原子控制段注册问题已解决。

---

## 修复内容（commit `90f1c905`）

`ep/src/uccl_proxy.cpp` MetaX 分支：atomic buffer 改为申请 90112B backing，返回其中 4KiB 对齐的地址作 81960B 控制段。新增 `ep/include/gpu_aligned_allocation.hpp`。probe 默认改用生产分配函数（`--probe-control production`）。

**v2_02 → v2_03 关键变化**：
```
v2_02: mcExtMallocWithFlags → va=0xa24a03600 (非4KiB对齐) → urma_register_seg 返回 NULL
v2_03: 对齐包装 → [URMA_ALLOC] aligned=0xa22400000 (4KiB对齐) → 注册成功
```

---

## 4 步流程结果

### Step 1 构建 v2_03 ✅
两机 `BUILD_PASSED`，新 `ep.abi3.so`（204 sha `4d17ae2b...`，含对齐分配 + `[URMA_ALLOC]` 诊断）。

### Step 2 生产分配探针 ✅
两机 `--probe-control production --probe-bytes 483892608`：
```
[URMA_ALLOC] control raw=0xa22400000 aligned=0xa22400000 logical=81960 backing=90112 alignment=4096 finegrained=1
[URMA_REG] kind=gpu va=0xa22400000 len=81960 atomic=1 → stage=success   ← 原子控制段注册成功
[URMA_REG] kind=gpu va=0xa04c00000 len=483892608 atomic=0 → stage=success ← 主数据段成功
REGISTRATION_PROBE_PASSED
```
- `finegrained=1` ✅ 走了生产细粒度分配路径
- `alignment=4096` ✅ 4KiB 对齐
- 4 transport 全成功（thread0 success，thread1-3 cache_hit）

### Step 3 双机 Normal ✅ `URMA_MODE_PARITY_ALL_PASSED`
```
CASE_END normal rc=0 checks={mode:True, done:True, exit_ack:True, no_errors:True,
  correctness:True, profiles:True, tuning:True, native_mode:True, device:True}
```
两机 rc=0，全 9 check True。

### Step 4 双机 LL ✅ `URMA_MODE_PARITY_ALL_PASSED`
```
CASE_END low_latency rc=0 checks={mode:True, done:True, exit_ack:True, no_errors:True,
  correctness:True, profiles:True, bandwidth:True, hook:True, native_mode:True, device:True}
```
两机 rc=0，全 11 check True（含 LL 特有的 bandwidth + hook）。

---

## 三轮迭代总结

| 轮次 | commit | 核心变化 | Normal | LL |
|---|---|---|---|---|
| v2_01 | b893b7f5 | 原始 | ❌ 注册失败（无诊断） | 未到达 |
| v2_02 | 20c8bfdf | +`[URMA_REG]` 诊断 + probe | ❌ 定位到 atomic VA 非对齐 | 未到达 |
| v2_03 | 90f1c905 | +对齐分配修复 | ✅ **PASSED** | ✅ **PASSED** |

**根因→修复链**：
1. v2_01：笼统 "P2P segment registration failed"，无法定位
2. v2_02：`[URMA_REG]` 诊断锁定 `kind=gpu atomic=1 va=0xa24a03600 errno=0`，发现 VA 非 4KiB 对齐
3. v2_03：对齐包装分配，`[URMA_ALLOC] aligned=0xa22400000`，注册成功 → 双模式全过

---

## 文件清单

- `SUCCESS_REPORT_v2_03.md` — 本报告
- `reg03_production_node{0,1}/` — 生产分配探针（probe.log 含 `[URMA_ALLOC]` + `[URMA_REG]`）
- `urma_normal_v2_03_node{0,1}/` — Normal 双机 run（bench.log, status.json, metrics.jsonl, kernel_*.txt）
- `urma_ll_v2_03_node{0,1}/` — LL 双机 run（同上）
