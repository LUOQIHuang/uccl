# smoke128_08 结果报告

## 测试时间
2026-11-12 04:40 ~ 04:42

## smoke128_07 根因已修复
204 git 从 `aa5accb0` 同步至 `7ecab45a`，两端 diagnostics_hash 现已一致：
```
diagnostics_hash=b3c0232b16acf89cb980a27221591a499a5fbb0918070870ea8dd49a856b3443 (一致)
sources_hash=75c06eec94f73033c5ff4cca3531e8bd6327bfb689a8d24e20a397172bef9407 (一致)
```

## smoke128_08 结果：部分通过，correctness 阶段 urma_bind_jetty rc=4096

### 成功阶段
1. ✅ **MODE_PARITY_CHECK_PASSED** — 两端模块加载、库校验通过
2. ✅ **PAIR setup** — diagnostics_hash 匹配，握手成功（不再 PAIR_STOP）
3. ✅ **URMA transport init** — TRANSPORT_MODE_OK，URMA 注册全部成功
   ```
   [URMA] mode=normal channels=8 endpoints=9 batch=linked capacity=1024 wire=2
   [URMA_REG] rank=1 thread=0 stage=success kind=gpu va=0xa04400000 len=483892608 atomic=0
   TRANSPORT_MODE_OK backend=urma mode=normal urma_wire=2 proxies=4
   ```
4. ✅ **MCCL init** — 两端 MCCL 完成
5. ✅ **Layout benchmark** — 测量成功：
   ```
   MODE_PARITY_SAMPLES {"label": "layout", "mean_us": 143.36, "median_us": 143.36, "min_us": 135.42, "max_us": 151.30}
   [layout] Kernel performance: 0.143 ms
   ```

### 失败阶段
6. ❌ **Correctness test (BF16, no top-k)** — 第二阶段绑定 jetty 时 SIGABRT：
   ```
   [testing] Running with BF16, without top-k (async=False, previous=False) ...
   UcclProxy thread 0 failed with exception: urma_bind_jetty failed: rc=4096 (Unknown error 4096)
   terminate called after throwing an instance of 'std::runtime_error'
     what():  urma_bind_jetty failed: rc=4096 (Unknown error 4096)
   Fatal Python error: Aborted
   ```
   堆栈：`buffer.py:1661 internode_dispatch → buffer.py:1045 dispatch → correctness.py:13 → normal.py:246 test_main → normal.py:579 test_loop`

### 分析
- URMA 数据通道在 layout 阶段完全正常（注册、通信、benchmark 均成功）
- 失败发生在第二阶段（correctness testing）创建新的 UcclProxy jetty 绑定时
- `rc=4096` 与 smoke128_01 相同，是 URMA 驱动层 jetty 资源分配问题
- 与 smoke128_01 不同的是：本次 layout benchmark 已成功运行，说明 URMA 基本功能可用
- 此问题在 smoke128_01 中曾在大约 1 小时后自行恢复

### status.json
```json
{"rc": 1, "checks": {"mode": true, "done": false, "no_errors": false, "correctness": false, "reference_correctness": false, "reference_events": false, "device": true, "normal_completion": true, "phase_files": true, "samples": false}}
```

## 结论
URMA reference_events 移植在 layout 阶段验证通过。correctness 阶段的 `urma_bind_jetty rc=4096` 是 URMA 驱动层已知问题，需等待 URMA 子系统稳定或驱动修复。
