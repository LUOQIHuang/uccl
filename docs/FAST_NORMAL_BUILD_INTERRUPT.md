# FAST_NORMAL 构建中断报告

**日期**: 2026-11-10
**测试**: FAST_NORMAL (按 FAST_NORMAL.zh-CN.md 第2节构建)
**Commit**: `2e63b8ba` "Add fast Normal CPU-only diagnostics and MACA precision build probe"
**BACKEND**: urma

## 问题

构建过程中两端同时不可用：
- **204**: 重启了（up 3 min），内核从 diag (`6.6.0-gdr_f00955680_diag`) 变为 verbs (`6.6.0-145.3.34.165`)。无 vmcore crash dump。可能是 OOM（编译内存不足）导致系统重启。
- **202**: ping 通但 SSH 端口拒绝连接（NoValidConnectionsError: Unable to connect to port 22）。可能 sshd 资源耗尽或内存压力。

## 构建进度（204 上收集到）

204 的 `fast_normal_build/urma_cpu_01/` 目录内容：
- `precision_probe_01.log` — **MACA_PRECISION_PROBE_PASSED** ✅（half 比较修复有效）
- `host_tests.log` — **所有 CPU 测试通过** ✅
- `build_request.json` — 构建请求
- `precision_probe_01/` — precision probe 输出目录
- **无 manifest.json** — 完整 GPU 编译未完成（崩溃在编译阶段）

202 的构建状态未知（SSH 不通）。

## 分析

1. **MACA half 比较修复有效**: precision_probe 通过，`maca_fp8_impl.hpp` 的 `__half` ambiguous 问题已解决
2. **崩溃发生在完整编译阶段**: precision probe（小编译）和 host tests（CPU测试）都通过，但完整 GPU 扩展编译（ninja -j 4）时系统崩溃
3. **可能是 OOM**: 204 无 vmcore，204 重启后内核变化，可能是内存不足导致内核杀进程或系统重启
4. **两端同时崩溃**: 202 和 204 在同一时间窗口内不可用，说明同时编译时内存压力过大

## 环境

| 项目 | 202 | 204 |
|------|-----|-----|
| 构建前内核 | diag | diag |
| 崩溃后内核 | 未知(SSH不通) | verbs (重启后变化) |
| 磁盘 | 8.9G free | 7.6G free |
| precision_probe | 未知 | ✅ PASSED |
| host_tests | 未知 | ✅ PASSED |
| 完整编译 | 未知 | ❌ 未完成 |

## 建议

1. **降低编译并发**: 将 ninja `-j 4` 改为 `-j 2` 或 `-j 1`，减少内存压力
2. **串行构建**: 两端不要同时构建，先在 202 构建完成后再在 204 构建
3. **检查内存**: 构建前检查 `free -h`，确保有足够内存
4. **202 需要恢复**: 202 SSH 不通，可能需要手动重启或等待 sshd 恢复
5. **204 内核需要切回 diag**: 当前为 verbs kernel，需切回 `6.6.0-gdr_f00955680_diag`

## 文件位置

- 204 precision_probe log: `test/gpu_stage_recovery/results/build_fail/node1/precision_probe_01.log`
- 204 host_tests log: `test/gpu_stage_recovery/results/build_fail/node1/host_tests.log`
- 本报告: `docs/FAST_NORMAL_BUILD_INTERRUPT.md`
