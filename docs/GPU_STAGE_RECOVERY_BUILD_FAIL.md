# GPU Stage Recovery v2 构建失败报告

**日期**: 2026-11-10
**测试**: gpu_stage_recovery (按 README.zh-CN.md 第3节构建)
**Commit**: `3a69481b` "Replace GPU host atomic tracing with private device slots and staged recovery"
**BACKEND**: urma, diag kernel `6.6.0-gdr_f00955680_diag`

## 问题

构建 `urma_base_v2` 时编译失败，错误来自 MACA 3.9.0 的 `maca_fp8_impl.hpp` 中 `__half` 比较 operator ambiguous。

## 错误信息

```
In file included from ep/include/ep_configs.cuh:82:
In file included from /opt/maca-3.9.0/tools/cu-bridge/include/cuda_fp8.h:4:
In file included from /opt/maca-3.9.0/tools/cu-bridge/include/cuda_fp8.hpp:14:
In file included from /opt/maca-3.9.0/include/common/maca_fp8.h:309:
In file included from /opt/maca-3.9.0/include/common/maca_fp8.hpp:10:
/opt/maca-3.9.0/include/common/maca_fp8_impl.hpp:69:25: error: use of overloaded operator '>' is ambiguous (with operand types '__half' and '__half')
   69 |     if (*(__half *)&val > MAXNORM) {
      |         ~~~~~~~~~~~~~~~ ^ ~~~~~~~
note: built-in candidate operator>(float, float)
note: built-in candidate operator>(float, long double)
note: built-in candidate operator>(float, __float128)
note: built-in candidate operator>(float, int)
note: built-in candidate operator>(float, long)
note: built-in candidate operator>(float, long long)
note: built-in candidate operator>(float, __int128)
```

同样的错误出现在 `maca_fp8_impl.hpp` 的第 69, 71, 95, 97 行。

## 环境

| 项目 | 值 |
|------|-----|
| MACA | 3.9.0 (`/opt/maca-3.9.0`) |
| torch | 2.6.0+metax3.7.0.7 |
| 内核 | 6.6.0-gdr_f00955680_diag |
| Python | 3.12 (venv-maca-py312) |

## 对比

之前 `urma_mode_parity` 的 `stage_base_v1` 构建成功（commit `2c9a10d9`），build.log 中没有 `maca_fp8_impl` 错误。新 commit `3a69481b` 新增了 `ep/src/gpu_stage_trace.cu` 和 `ep/src/internode.cu` 改动，可能引入了新的编译路径触发 `cuda_fp8.h` → `maca_fp8_impl.hpp` 的 `__half` 比较 ambiguous。

## 状态

构建未通过，无法进入第4节测试步骤。完整 build.log 已上传。
