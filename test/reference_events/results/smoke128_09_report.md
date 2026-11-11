# smoke128_09 结果报告

## 测试时间
2026-11-12 04:47 ~ 04:49

## 结果：与 smoke128_08 相同 — layout 通过，correctness urma_bind_jetty rc=4096

### 成功阶段
1. ✅ MODE_PARITY_CHECK_PASSED
2. ✅ PAIR setup（diagnostics_hash 一致）
3. ✅ URMA transport init（TRANSPORT_MODE_OK）
4. ✅ MCCL init
5. ✅ **Layout benchmark** — 113.28us mean（比 smoke128_08 的 143.36us 更快）

### 失败阶段
6. ❌ Correctness test (BF16, no top-k) — `urma_bind_jetty failed: rc=4096`

### 204 dmesg 错误
```
ubcore_exchange_tp_info[835]|Failed to send create req message, ret: -1.
ubcore_bind_jetty_reuse_compat[2268]|Failed to exchange tp info, ret: -1.
uburma_cmd_bind_jetty_ex:[3756]|bind jetty failed, ret: -1.
ubcore_session_timeout[128]|Session 3 timeout
```

## smoke128_08 vs 09 对比
| | smoke128_08 | smoke128_09 |
|---|---|---|
| layout mean_us | 143.36 | 113.28 |
| layout min_us | 135.42 | 108.03 |
| correctness | urma_bind_jetty rc=4096 | urma_bind_jetty rc=4096 |
| 204 uptime | 28min | 37min |

## 结论
URMA 数据通道（layout benchmark）持续正常且性能稳定提升（143→113us），但 correctness 阶段的第二次 jetty 绑定始终失败。此为 URMA 驱动层 MAD TP exchange 问题，204 uptime 不足1小时，smoke128_01 曾在约1小时后自行恢复。
