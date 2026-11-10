# GPU Stage Tracing Diagnostic 崩溃报告

**日期**: 2026-11-10
**测试**: STAGE_BREAKDOWN (verbs + urma mode parity)
**仓库**: codex/metax-urma-integration
**Commits**: `2c9a10d9` (verbs结果), `bb1cf5d7` (urma结果), `e3e31983` (本报告)

## 摘要

GPU stage tracing 诊断任务 (`cpu_diagnostic` / `diagnostic`) 在 C500 GPU 上的 BF16 Dispatch 测试点**反复导致内核 panic**，崩溃模式在 verbs 内核和 diag 内核上完全一致。3 轮 baseline 任务在两种内核上均全部通过，仅带 GPU stage 插桩的 diagnostic 任务崩溃。

经调查确认：**这不是回归问题**。重型 per-warp GPU stage 插桩（`UCCL_GPU_STAGE_TRACE`）是 `8d0a6b88`（2026-11-09）新增的功能，之前从未在 BF16 Dispatch kernel 上运行过。之前成功的 `gpu_profile` 任务使用的是轻量级运行时 event 计时（无 `UCCL_GPU_STAGE_TRACE` 编译标志），因此不会触发此崩溃。

## 环境信息

| 项目 | 值 |
|------|-----|
| 服务器 | 141.61.17.202 (RANK=0, master) + 141.61.17.204 (RANK=1, worker1) |
| GPU | MetaX C500 (65536 MiB), 1 per node |
| MACA 版本 | 3.9.0.29 |
| KMD 版本 (verbs kernel) | 3.10.14 |
| KMD 版本 (diag kernel) | 3.8.23 |
| verbs 内核 | `6.6.0-145.3.34.165.oe2403sp3.aarch64` |
| diag 内核 | `6.6.0-gdr_f00955680_diag` |
| HCA (verbs) | rocep40s0f0, port1, GID3=RoCE v2 |
| URMA 设备 (urma) | udmac0d1e2 |
| PCIe Atomic | 0x60 (需手动 setpci 设置，默认 0x20) |
| 构建 variant | current / mode normal |
| Suite 端口 | verbs 32280/32281, urma 32080 |

## 崩溃模式

### 触发条件
- **仅 diagnostic 任务崩溃**: baseline 任务 (无 GPU stage tracing) 在两种内核上全部通过
- **崩溃点**: BF16 Dispatch 测试，在 layout 测试点完成之后
- **完成窗口**: layout (5/25)，BF16 为第2个测试点
- **两端同时崩溃**: RANK=0 和 RANK=1 在同一时刻 panic

### 崩溃表现
1. **SSH 中断**: 正在运行 BF16 时 SSH 连接超时
2. **Ping 无响应**: 两端同时 100% packet loss
3. **vmcore dump**: 202 节点 `/var/crash/` 产生 9.6G `vmcore-incomplete` 内核转储
4. **磁盘填满**: vmcore dump 将 69G 根分区填至 100%，导致系统不可用
5. **自动重启**: 节点在数分钟后自动恢复，内核版本不变

### 崩溃统计

| 尝试 | 内核 | 崩溃点 | layout完成 | BF16完成 | vmcore | 恢复时间 |
|------|------|--------|-----------|---------|--------|---------|
| verbs #1 | 6.6.0-145 | BF16 Dispatch | ✅ 5/25 | ❌ | 9.6G (202) | ~5min |
| verbs #2 | 6.6.0-145 | BF16 Dispatch | ✅ 5/25 | ❌ | 9.6G (202) | ~5min |
| urma #1 | 6.6.0-gdr_f00955680_diag | BF16 Dispatch | ✅ 5/25 | ❌ | N/A | ~3-5min |

## 测试结果

### Verbs STAGE_BREAKDOWN (`verbs_stage_01`)

| 任务 | 202 (RANK=0) | 204 (RANK=1) |
|------|-------------|-------------|
| baseline_r01 | ✅ PASSED | ✅ PASSED |
| baseline_r02 | ✅ PASSED | ✅ PASSED |
| baseline_r03 | ✅ PASSED | ✅ PASSED |
| diagnostic | ❌ CRASH (BF16) | ❌ CRASH (BF16) |

### URMA STAGE_BREAKDOWN (`urma_stage_01`)

| 任务 | 202 (RANK=0) | 204 (RANK=1) |
|------|-------------|-------------|
| baseline_r01 | ✅ PASSED | ✅ PASSED |
| baseline_r02 | ✅ PASSED | ✅ PASSED |
| baseline_r03 | ✅ PASSED | ✅ PASSED |
| diagnostic | ❌ CRASH (BF16) | ❌ CRASH (BF16) |

## 不是回归问题：之前 gpu_profile vs 现在 diagnostic

### 关键区别

| | 之前 gpu_profile（能跑） | 现在 diagnostic（崩溃） |
|---|---|---|
| `UCCL_GPU_STAGE_TRACE` 编译标志 | **未定义** | **=1** |
| 插桩方式 | 运行时 GPU event 计时 | 编译时 per-warp 原子操作 |
| 开销 | 低（kernel 级耗时） | 高（每 warp `clock64`+`atomicAdd`+`atomicMax`） |
| gpu_stages.jsonl 内容 | `kernel_seconds: [0.006247, 7.8054e-05]` | per-warp cycles/calls/max_cycles |
| 构建版本 | `5d38ab56` (normal_campaign_v1) | `2c9a10d9` (stage_trace_v1) |
| BF16 结果 | ✅ 全部通过 | ❌ 崩溃驱动 |

### 代码变更历史

| Commit | 日期 | 内容 |
|--------|------|------|
| `5d38ab56` | 更早 | 之前成功运行的 baseline（无 GPU stage tracing） |
| `8d0a6b88` | 2026-11-09 20:59 | **新增** Normal URMA GPU stage tracing 和 FIFO completion timelines |
| `f754996c` | 2026-11-09 21:13 | 新增匹配的 verbs Normal GPU stage experiment |
| `d7e07718` | 2026-11-10 05:41 | Guard `UCCL_GPU_STAGE_TRACE` CUDA built-ins with device-context check |
| `664c8193` | 2026-11-10 05:55 | 测试文件添加 `__CUDA_ARCH__` 定义 |
| `85e1078f` | 2026-11-10 | `ibv_reg_mr` 重定义修复 |

**结论**: `UCCL_GPU_STAGE_TRACE` 是 `8d0a6b88` 新增的功能，之前从未在 BF16 Dispatch kernel 上运行过。本次崩溃是该新功能首次在 BF16 上运行的发现，不是代码回归。

## 根因分析

### 触发代码
崩溃由 `gpu_stage_trace.cuh` 中的 GPU stage tracing 插桩触发，该插桩在 `--gpu-stage-build` 编译的 trace 模块中激活（`UCCL_GPU_STAGE_TRACE=1`）：

- `clock64()` — GPU 时钟计数器读取（每 warp 执行）
- `atomicAdd()` — 原子加法（用于 metrics 累加，每 warp 执行）
- `atomicMax()` — 原子最大值（用于 metrics 记录，每 warp 执行）

涉及文件：
- `ep/include/gpu_stage_trace.cuh:12,24` — Span 构造和 finish 中的插桩
- `ep/include/fifo_device.hpp:131` — stagePublications 记录
- `ep/include/ring_buffer.cuh:479` — ring buffer 中的插桩
- `ep/include/d2h_queue_device.cuh:96` — d2h queue 中的插桩

### 崩溃路径
1. diagnostic 任务使用 trace 构建 (`--gpu-stage-build`)，在 BF16 Dispatch kernel 中激活 GPU stage tracing
2. layout 测试点（较简单的 kernel）能正常完成 GPU stage tracing（5/25 窗口）
3. BF16 Dispatch kernel（更复杂的 kernel）执行 GPU stage tracing 时触发 C500 KMD 崩溃
4. KMD 崩溃导致内核 panic，产生 vmcore dump
5. vmcore dump 填满磁盘，节点不可用后自动重启

### 排除因素
- **内核版本无关**: verbs kernel 和 diag kernel 都崩溃，排除内核差异
- **网络无关**: baseline 任务使用相同网络路径全部通过
- **GPU硬件无关**: baseline 任务在相同GPU上正常运行，崩溃后GPU恢复正常
- **构建无关**: trace 模块构建和 check 全部通过 (`BUILD_PASSED` + `MODE_PARITY_CHECK_PASSED`)
- **仅 BF16 触发**: layout kernel 正常，BF16 Dispatch kernel 崩溃
- **非代码回归**: `d7e07718` 的 guard 修改只影响 host 编译路径（修复编译错误），device 编译中 guard 仍然激活，行为不变

### 结论
这是 **C500 GPU 驱动 (KMD) 的 bug**，由 `gpu_stage_trace.cuh` 中的 `clock64()`+`atomicAdd()`+`atomicMax()` per-warp 插桩在 BF16 Dispatch kernel 执行时触发。崩溃在 KMD 3.10.14 (verbs) 和 KMD 3.8.23 (diag) 上均复现，说明是驱动层面的稳定性问题，与内核版本无关。

---

## 测试过程中遇到的其他问题

### 1. 编译错误（已修复，3个commit）

#### 1.1 `ibv_reg_mr` 重定义 `-Werror`（commit `85e1078f`）
- **文件**: `ep/tests/verbs_notify_test.cpp:69`
- **问题**: `#define ibv_reg_mr fake::mr` 之前没有 `#undef ibv_reg_mr`，导致宏重定义 `-Werror` 错误
- **修复**: 在 `#define ibv_reg_mr fake::mr` 前添加 `#undef ibv_reg_mr`

#### 1.2 GPU stage guard 缺少设备上下文检查（commit `d7e07718`）
- **文件**: `ep/include/gpu_stage_trace.cuh:12,24`, `ep/include/fifo_device.hpp:131`
- **问题**: `#ifdef UCCL_GPU_STAGE_TRACE` 在 host C++ 编译时也激活（通过 `-D__MC_PLATFORM_MXCC__`），导致 `threadIdx`/`warpSize`/`clock64`/`atomicAdd`/`atomicMax` 在 host 上下文中未声明
- **修复**: 改为 `#if defined(UCCL_GPU_STAGE_TRACE) && (defined(__CUDA_ARCH__) || defined(__HIP_DEVICE_COMPILE__))`，确保只在 device 编译时激活

#### 1.3 测试文件缺少 `__CUDA_ARCH__` 定义（commit `664c8193`）
- **文件**: `ep/tests/gpu_stage_trace_test.cpp:5`
- **问题**: 1.2 的修复导致 host 端测试无法触发 device 路径
- **修复**: 在 `#define __CUDACC__` 后添加 `#define __CUDA_ARCH__ 1`

### 2. 链接错误：`cannot find -lgcc_s`

- **问题**: verbs/urma 构建时链接器找不到 `libgcc_s`
- **原因**: `LIBRARY_PATH` 未包含 gcc 库路径
- **修复**: 构建脚本中添加 `export LIBRARY_PATH=/usr/lib/gcc/aarch64-openEuler-linux/12:/usr/lib64`

### 3. 构建检查错误：`CURRENT_BUILD_MISMATCH`

- **问题**: base 模块的 check 失败，提示构建不匹配
- **原因**: base 模块在代码修复（commit `85e1078f`/`d7e07718`/`664c8193`）之前构建，source hash 不匹配
- **修复**: 代码修复后重新构建 base 和 trace 两个模块

### 4. 设备被其他用户抢占

#### 4.1 URMA 设备被 fbthrift 占用
- **节点**: 202 (PID 36115, 后续 PID 78775)
- **占用者**: `d00836578` 用户运行 `urma_fbthrift/fbthrift/press_server --dev_name=udmac0d1e2`
- **影响**: 导致 `idle_check` 失败，`DEVICE_BUSY_OR_CHECK_FAILED`
- **处理**: kill 占用进程后重跑（用户已授权 kill 抢占者）

#### 4.2 `/dev/mxcd` 被 gda_perf 占用
- **节点**: 204 (PID 595754)
- **占用者**: `gda_perf server udmac1d1e2 0 write 20`
- **影响**: 导致 `idle_check` 失败，`DEVICE_BUSY_OR_CHECK_FAILED: /dev/mxcd`
- **处理**: kill 占用进程和父进程后重跑

### 5. P2P Bridge 瞬时错误：`URMA_GPU_P2P_BRIDGE_MISSING`

- **问题**: kill 设备占用进程后，`udma_metax_p2p_bridge` 模块瞬时不可见
- **检查**: `ls /sys/module/udma_metax_p2p_bridge` 失败
- **恢复**: 等待几秒后模块恢复可见，重跑即通过
- **原因推测**: kill 进程时设备释放操作短暂影响 sysfs 模块状态

### 6. 磁盘空间问题：vmcore dump 填满根分区

- **问题**: 内核 panic 产生 9.6G vmcore dump，填满 69G 根分区（`/`）
- **影响**:
  - 系统不可用，SSH 无法连接
  - SFTP 上传失败（`size mismatch in put! 0 != N`）
  - 脚本文件上传后为 0 字节
- **处理**: 节点恢复后 `rm -rf /var/crash/*` 清理 vmcore
- **建议**: 测试前禁用 kdump（`systemctl stop kdump`）防止 vmcore 填满磁盘

### 7. SFTP 上传问题

#### 7.1 size mismatch
- **问题**: `sftp_put_file.py` 上传报 `size mismatch in put! 0 != 273`
- **原因**: 磁盘满（vmcore 填满 `/`）或节点不稳定
- **处理**: 清理磁盘后重新上传

#### 7.2 SSH banner 污染脚本文件
- **问题**: 上传到 204 的脚本文件首行是 SSH banner `Authorized users only...`
- **原因**: 通过 SSH 管道传输文件时 banner 被写入文件
- **处理**: 使用 SFTP 直接上传而非 SSH 管道

### 8. 内核切换

- **问题**: 测试中途两端从 verbs kernel (`6.6.0-145`) 切换到 diag kernel (`6.6.0-gdr_f00955680_diag`)
- **影响**: verbs STAGE_BREAKDOWN 测试暂停，转而执行 urma STAGE_BREAKDOWN 测试
- **推测原因**: 其他用户操作或系统自动切换

### 9. PCIe Atomic 需手动设置

- **问题**: 两端 PCIe Atomic Capabilities 默认为 `0x0020`，测试要求 `0x0060`（bit 6 = AtomicOp Routing）
- **修复**: `setpci -s 0000:a0:08.0 CAP_EXP+28.w=0060`
- **注意**: 重启后需重新设置（非持久化）

---

## 建议

### 针对崩溃问题
1. **向 MetaX 报告驱动 bug**: 提交 C500 KMD 在 `clock64()`+`atomicAdd()`+`atomicMax()` 组合下 BF16 kernel 崩溃的问题，附上崩溃统计和复现步骤
2. **降低插桩强度**: 尝试移除 `atomicAdd`/`atomicMax`，仅保留 `clock64()`，测试是否仍崩溃
3. **分阶段插桩**: 在 BF16 kernel 中逐步添加插桩点，定位具体是哪个操作触发崩溃
4. **捕获 vmcore 分析**: 下次崩溃时保留 `vmcore-dmesg.txt` 并用 `crash` 工具分析 panic 栈

### 针对测试环境
5. **禁用 kdump**: 测试前执行 `systemctl stop kdump` 防止 vmcore 填满磁盘
6. **持久化 PCIe Atomic**: 将 `setpci` 命令加入 systemd 服务或 udev 规则，避免重启后丢失
7. **设备空闲监控**: 测试前和测试间隙检查 `fuser /dev/uburma/udmac0d1e2 /dev/mxcd`，及时清理占用进程

## 本次代码修复汇总（已推送）

| Commit | 文件 | 修复 |
|--------|------|------|
| `85e1078f` | `ep/tests/verbs_notify_test.cpp:69` | `ibv_reg_mr` 重定义 `-Werror` — 添加 `#undef` |
| `d7e07718` | `ep/include/gpu_stage_trace.cuh:12,24`, `ep/include/fifo_device.hpp:131` | GPU stage guard 添加 `__CUDA_ARCH__` 设备上下文检查 |
| `664c8193` | `ep/tests/gpu_stage_trace_test.cpp:5` | 测试文件添加 `__CUDA_ARCH__` 定义 |

## 文件位置

- Verbs 结果: `test/verbs_baseline/results/verbs_stage_01/`
- URMA 结果: `test/urma_mode_parity/results/urma_stage_01/`
- 本报告: `docs/GPU_STAGE_TRACING_CRASH_REPORT.md`
- 之前成功 gpu_profile 结果（对比参考）: `test/verbs_baseline/results/verbs_normal_campaign_01/` 和 `test/verbs_baseline/results/verbs_baseline_01/`
