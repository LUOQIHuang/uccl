# URMA Mode Parity v2_02 — GPU 原子控制段注册失败定位

- **测试**: `test/urma_mode_parity` v2_02（按 `REGISTRATION_FAILURE.zh-CN.md` 流程）
- **时间**: 2026-11-05 03:30–03:38
- **结论**: **根因定位到代码层**——GPU 原子控制段 VA 非 4KiB 对齐导致 `urma_register_seg(atomic=1)` 返回 NULL。`mcExtMallocWithFlags(mcDeviceMallocFinegrained)` 不保证 4KiB 对齐。可在 `ep/src/uccl_proxy.cpp` 侧修复（对齐分配或页对齐 wrapper），非闭源 liburma 黑盒。

---

## 1. 流程执行结果

### Step 1 构建（v2_02）✅
两机 `BUILD_PASSED`，新 `ep.abi3.so` 含 `[URMA_REG]` 诊断 + `probe_urma_registration` 入口。

### Step 2 单机探针（4 项 × 2 机）✅ 全过

| Probe | 配置 | 202(node0) | 204(node1) |
|---|---|---|---|
| A reg02_ll1 | LL 1ch × 1tr | ✅ PASSED | ✅ PASSED |
| B reg02_n8_1 | Normal 8ch × 1tr | ✅ PASSED | ✅ PASSED |
| C reg02_n1_4 | Normal 1ch × 4tr | ✅ PASSED | ✅ PASSED |
| D reg02_n8_4 | Normal 8ch × 4tr（生产配置）| ✅ PASSED | ✅ PASSED |

probe D（生产配置 4 transport）成功注册：GPU 主段(va=0xa04c00000,112MB,atomic=0)✓ + GPU 控制段(va=0xa0c000000,81960,atomic=1)✓，3 线程 cache_hit。**证明注册逻辑在 VA 对齐时完全正常。**

### Step 3 双机 Normal run ❌ 注册阶段崩溃

两机均到达 CASE_END（`native_mode=true, device=true`），但 rc=1。崩溃在 GPU 原子控制段注册。

---

## 2. 精确失败点（`[URMA_REG]` 诊断锁定）

### 成功的注册（两机一致）
```
[URMA_REG] kind=gpu va=0xa04c00000 len=483892608 atomic=0 → stage=success   ← GPU 主数据段 ✓
[URMA_REG] kind=host va=0xfffa... len=8192/64 atomic=0/1 → stage=success    ← host 段全过 ✓
```

### 失败的注册（两机一致，3 线程全失败）
```
[URMA_REG] stage=begin kind=gpu va=0xa24a03600 len=81960 atomic=1 access=14
[URMA_REG] stage=failed rank=1 thread=3 atomic=1 len=81960 errno=0 (provider did not set errno)
[URMA_REG] stage=failed rank=1 thread=2 atomic=1 len=81960 errno=0 (provider did not set errno)
[URMA_REG] stage=failed rank=1 thread=1 atomic=1 len=81960 errno=0 (provider did not set errno)
```
- **段类型**: `kind=gpu atomic=1`（GPU 原子控制段，`URMA_ACCESS_ATOMIC`）
- **VA**: `0xa24a03600` — **非 4KiB 对齐**（末位 `0x600`，对齐值应是 `0x...000`）
- **len**: 81960（`kAtomicBufferSize`，`ep/include/common.hpp:61`）
- **errno**: `0`（liburma 用户态校验拒绝，未进内核、未设 errno）

### 内核日志佐证
`kernel_after.txt` 中有 GPU 主段的 `ubcore_register_seg`+`pin OK`，但**失败段 `0xa24a03600` 无任何 `ubcore_register_seg` 记录** → `urma_register_seg` 在 ioctl 之前（用户态）就拒绝了，与"对齐校验在 liburma 用户态完成"一致。

---

## 3. 根因：atomic buffer 分配未对齐

### 分配路径
`ep/src/uccl_proxy.cpp:74`（MetaX 分支）：
```cpp
mcExtMallocWithFlags(&atomic_buffer_ptr_, kAtomicBufferSize,
                     mcDeviceMallocFinegrained);
```
`mcExtMallocWithFlags(mcDeviceMallocFinegrained)` 分配细粒度 GPU 内存，返回 VA = `0xa24a03600`（非 4KiB 对齐）。

### 注册路径
`ep/src/proxy.cpp:282` → `UrmaTransport::register_atomic_buffer(ptr, 81960, device)` → `register_segment(ptr, len, gpu=true, atomic=true)` → `urma_register_seg(cfg)` 对 `va=0xa24a03600 atomic=1` 返回 NULL。

### probe 过、benchmark 不过的原因
| | control 段 VA | 对齐 | atomic 注册 |
|---|---|---|---|
| **probe** | `torch.empty(86016)` 的 data_ptr | 4KiB（torch 分配器保证）| ✅ 成功 |
| **benchmark** | `mcExtMallocWithFlags` 返回 `0xa24a03600` | **非 4KiB**（末位 0x600）| ❌ 失败 |

URMA 原子段（`URMA_ACCESS_ATOMIC`）要求 VA 对齐（原子操作需对齐访问）。torch 分配器对齐，MetaX `mcExtMallocWithFlags` 不保证对齐 → 唯一差异。

### errno=0 的含义
不是资源耗尽（会 ENOMEM/ENOSPC），是 liburma 用户态对齐/参数校验拒绝，该路径未设 errno → 与"用户态对齐校验"一致。

---

## 4. 修复方向（代码层，非闭源）

在 `ep/src/uccl_proxy.cpp` MetaX 分支，将 `mcExtMallocWithFlags` 包装为页对齐分配，或改用保证对齐的分配器：

```cpp
// 方案 A：分配 (kAtomicBufferSize + 0x1000) 后页对齐
mcExtMallocWithFlags(&raw, kAtomicBufferSize + 0xFFF, mcDeviceMallocFinegrained);
atomic_buffer_ptr_ = reinterpret_cast<void*>(
    (reinterpret_cast<uintptr_t>(raw) + 0xFFF) & ~uintptr_t(0xFFF));
// 需保留 raw 供释放

// 方案 B：检查 mcExtMallocWithFlags 是否有对齐参数/flag

// 方案 C：allocate_atomic_buffer (gpu_p2p_runtime) 内做对齐
```

核心：确保传给 `register_atomic_buffer` 的 `ptr` 是 4KiB（或更大页）对齐的 GPU 地址。

---

## 5. v2_01 → v2_02 进展对比

| 维度 | v2_01（旧） | v2_02（新诊断） |
|---|---|---|
| 诊断 | 无 `[URMA_REG]`，笼统 "P2P segment registration failed" | `[URMA_REG] stage=begin/failed` 精确到段类型/VA/len/errno |
| 失败段 | 无法区分主段还是控制段 | **GPU 原子控制段** (atomic=1, len=81960) |
| GPU 主段 | 推测失败 | **成功** (va=0xa04c00000, 483892608) |
| errno | 不可见 | `0` (provider did not set) |
| 根因 | 闭源 liburma 黑盒 | **VA 非 4KiB 对齐**（mcExtMallocWithFlags）|
| 可修复性 | 需厂商 | **代码层可修**（uccl_proxy.cpp 对齐分配）|

---

## 6. 文件清单

### 本次上传（v2_02）
- `INCIDENT_REPORT_v2_02.md` — 本报告
- `node0_202_normal/bench.log` — 202 完整 `[URMA_REG]` 序列 + 崩溃 trace
- `node0_202_normal/status.json` / `urma_preflight.json` / `kernel_after.txt`
- `node1_204_normal/bench.log` — 204 完整 `[URMA_REG]` 序列
- `node1_204_normal/status.json` / `urma_preflight.json` / `kernel_after.txt`
- `reg02_*/node{0,1}/probe.log` — 8 项 probe 诊断（全 PASSED，对照用）
- `reg02_*/node{0,1}/status.json` / `kernel_*.txt`

### 关键代码位置
- `ep/src/uccl_proxy.cpp:74` — atomic buffer 分配（mcExtMallocWithFlags，对齐问题源头）
- `ep/src/proxy.cpp:282` — register_atomic_buffer 调用
- `ep/src/urma_transport.cpp:438-446` — register_atomic_buffer → register_segment(atomic=true)
- `ep/src/urma_transport.cpp:388-415` — register_segment + `[URMA_REG]` 诊断 + errno 捕获
- `ep/include/common.hpp:61` — `kAtomicBufferSize 81960`
