# GPU Stage Tracing Diagnostic 崩溃报告

**日期**: 2026-11-10
**测试**: STAGE_BREAKDOWN (verbs + urma mode parity)
**仓库**: codex/metax-urma-integration
**Commits**: `2c9a10d9` (verbs结果), `bb1cf5d7` (urma结果)

## 摘要

GPU stage tracing 诊断任务 (`cpu_diagnostic` / `diagnostic`) 在 C500 GPU 上的 BF16 Dispatch 测试点**反复导致内核 panic**，崩溃模式在 verbs 内核和 diag 内核上完全一致。3 轮 baseline 任务在两种内核上均全部通过，仅带 GPU stage 插桩的 diagnostic 任务崩溃。

## 环境信息

| 项目 | 值 |
|------|-----|
| 服务器 | 141.61.17.202 (RANK=0) + 141.61.17.204 (RANK=1) |
| GPU | MetaX C500 (65536 MiB) |
| MACA 版本 | 3.9.0.29 |
| KMD 版本 (verbs) | 3.10.14 |
| KMD 版本 (diag) | 3.8.23 |
| verbs 内核 | `6.6.0-145.3.34.165.oe2403sp3.aarch64` |
| diag 内核 | `6.6.0-gdr_f00955680_diag` |
| HCA | rocep40s0f0 (verbs), udmac0d1e2 (urma) |
| 构建 variant | current / mode normal |

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

## 根因分析

### 触发代码
崩溃由 `gpu_stage_trace.cuh` 中的 GPU stage tracing 插桩触发，该插桩在 `--gpu-stage-build` 编译的 trace 模块中激活：

- `clock64()` — GPU 时钟计数器读取
- `atomicAdd()` — 原子加法（用于 metrics 累加）
- `atomicMax()` — 原子最大值（用于 metrics 记录）

### 崩溃路径
1. diagnostic 任务使用 trace 构建 (`--gpu-stage-build`)，在 BF16 Dispatch kernel 中激活 GPU stage tracing
2. layout 测试点（较简单的 kernel）能正常完成 GPU stage tracing
3. BF16 Dispatch kernel（更复杂的 kernel）执行 GPU stage tracing 时触发 C500 KMD 崩溃
4. KMD 崩溃导致内核 panic，产生 vmcore dump

### 排除因素
- **内核版本无关**: verbs kernel 和 diag kernel 都崩溃，排除内核差异
- **网络无关**: baseline 任务使用相同网络路径全部通过
- **GPU硬件无关**: baseline 任务在相同GPU上正常运行，崩溃后GPU恢复正常
- **构建无关**: trace 模块构建和 check 全部通过 (`BUILD_PASSED` + `MODE_PARITY_CHECK_PASSED`)
- **仅 BF16 触发**: layout kernel 正常，BF16 Dispatch kernel 崩溃

### 结论
这是 **C500 GPU 驱动 (KMD) 的 bug**，由 `gpu_stage_trace.cuh` 中的 `clock64()`/`atomicAdd()`/`atomicMax()` 插桩在 BF16 Dispatch kernel 执行时触发。崩溃在 KMD 3.10.14 (verbs) 和 KMD 3.8.23 (diag) 上均复现，说明是驱动层面的稳定性问题。

## 建议

1. **向 MetaX 报告驱动 bug**: 提交 C500 KMD 在 `clock64()`+`atomicAdd()`+`atomicMax()` 组合下 BF16 kernel 崩溃的问题
2. **降低插桩强度**: 尝试移除 `atomicAdd`/`atomicMax`，仅保留 `clock64()`，测试是否仍崩溃
3. **分阶段插桩**: 在 BF16 kernel 中逐步添加插桩点，定位具体是哪个操作触发崩溃
4. **捕获 vmcore 分析**: 下次崩溃时保留 `vmcore-dmesg.txt` 并用 `crash` 工具分析 panic 栈

## 本次代码修复 (已推送)

本次测试过程中修复了3个编译问题（均在 verbs 和 urma 构建中生效）：

| Commit | 修复 |
|--------|------|
| `85e1078f` | `ibv_reg_mr` 重定义 `-Werror` — 添加 `#undef` |
| `d7e07718` | GPU stage guard 添加 `__CUDA_ARCH__` 设备上下文检查 |
| `664c8193` | 测试文件添加 `__CUDA_ARCH__` 定义 |

## 文件位置

- Verbs 结果: `test/verbs_baseline/results/verbs_stage_01/`
- URMA 结果: `test/urma_mode_parity/results/urma_stage_01/`
- 本报告: `docs/GPU_STAGE_TRACING_CRASH_REPORT.md`
