# REFERENCE_EVENTS smoke128 失败：URMA jetty 绑定失败 (urma_bind_jetty rc=4096)

**测试**: 参考版本移植 Normal Dispatch 事件打点 (README.zh-CN.md §4 128-token 验证)
**模块**: urma_events_v1 (reference_events_version=1, gpu_stage_version=0)
**节点**: 202 (141.61.17.202, RANK=0) + 204 (141.61.17.204, RANK=1)
**内核**: 6.6.0-gdr_f00955680_diag (双端一致)
**代码版本**: commit aa5accb0
**时间**: 2026-11-12 02:22~02:24
**JOB**: urma_ref_smoke128_01

## 编译与检查 (第3节) — 通过

双端 base 和 trace 模块均编译成功，manifest.json 存在。

| 模块 | 节点 | module_sha | 大小 | 结果 |
|------|------|-----------|------|------|
| urma_base_v1 | 202 (node0) | — | 32157688 B | BUILD_PASSED |
| urma_base_v1 | 204 (node1) | — | 32157688 B | BUILD_PASSED |
| urma_events_v1 | 202 (node0) | 1ffede4964f02b490e47eb0d9ff501944bc575cd8e9b5f4a1423e61166a05d0c | — | BUILD_PASSED, reference_events_version=1 |
| urma_events_v1 | 204 (node1) | 9b136979cd694bdc4e9004bcd15e5ea0558fbb0c672b9a080025ed05f02141cd | — | BUILD_PASSED, reference_events_version=1 |

MODE_PARITY_CHECK_PASSED 双端均通过（import、硬件、SDK 校验均正常）。

## 环境状态

- PCIe AtomicOp: 双端 0060 (已设置)
- URMA 链路: rocep40s0f0 port1 ACTIVE/LinkUp，192.168.17.x 互联 ping 0% 丢包
- GPU: MXC500 Available (202), 双端可被 mx-smi 识别
- 内核模块: ubcore/uburma/ipourma/udma/metax 已加载
- 202 uptime: 8 min (刚重启), 204 uptime: 13 min (刚重启)

## 失败详情

### 错误现象

128-token smoke 测试 (trace, warmup1/samples3) 双端同时以 rc=1 失败。layout API 测量正常完成 (135us)，但在进入 BF16 dispatch 正确性测试时 UcclProxy 线程崩溃：

```
[testing] Running with BF16, without top-k (async=False, previous=False) ...
UcclProxy thread 0 failed with exception: urma_bind_jetty failed: rc=4096 (Unknown error 4096)
terminate called after throwing an instance of 'std::runtime_error'
  what():  urma_bind_jetty failed: rc=4096 (Unknown error 4096)
Fatal Python error: Aborted
```

进程收到 SIGABRT (exitcode -6)，导致 event_trace.jsonl 未生成，reference_events 检查失败。

双端错误完全对称 (202 thread 0, 204 thread 1)。

### 内核日志 (dmesg)

URMA MAD 传输路径交换失败，导致 jetty 绑定失败：

```
# 202 (node0)
[158.587449] URMA|ubcore|2484|2484|ubcore_session_timeout[128]|Session 3 timeout
[158.595945] URMA|ubcore|29570|29570|ubcore_exchange_tp_info[835]|Failed to send create req message, ret: -1.
[158.596564] URMA|ubcore|2484|2484|ubcore_session_timeout[128]|Session 1 timeout
[158.596005] URMA|ubcore|29570|29570|ubcore_bind_jetty_reuse_compat[2268]|Failed to exchange tp info, ret: -1.
[158.598315] URMA|ubcore|2484|2484|ubcore_session_timeout[128]|Session 4 timeout
[158.598324] URMA|ubcore|29568|29568|ubcore_exchange_tp_info[835]|Failed to send create req message, ret: -1.
[158.598331] URMA|ubcore|29568|29568|ubcore_bind_jetty_reuse_compat[2268]|Failed to exchange tp info, ret: -1.
[158.598338] URMA|uburma|29568|29568|uburma_cmd_bind_jetty_ex:[3756]|bind jetty failed, ret: -1.

# 204 (node1) — 对称失败
[434.264066] URMA|ubcore|31816|31816|ubcore_bind_jetty_reuse_compat[2268]|Failed to exchange tp info, ret: -1.
[434.264072] URMA|uburma|31816|31816|uburma_cmd_bind_jetty_ex:[3756]|bind jetty failed, ret: -1.
```

URMA MAD 初始化阶段也有持续错误（自开机起）：

```
ipourma0: ipourma set jetty priority failed. the priority of ctp cannot be set to 6
create tx jetty error, dev: ipourma0, i = 0
ubmad_ubc_eid_ops:[305]|Invalid eid 0000:0000:0001:0200:0010:0000:df00:0200.
ubmad_process_conn_single[1503]|get tjetty failed. eid 0000:0000:003f:0200:0010:0000:df00:4b00
ubmad_recv_work_handler:[1732]|process msg failed
```

### 尝试过的修复

| 修复操作 | 结果 |
|---------|------|
| 重载 udma/metax/p2p_bridge 模块 (202) | 模块重载成功，未解决 |
| 重载 udma/metax/p2p_bridge 模块 (204) | udma busy 无法 rmmod |
| 设置 PCIe atomic 0060 | 环境正确但测试仍失败 |
| 杀掉所有设备占用进程 | 双端无残留进程，未解决 |

## 对比：此前 dispatch_diag 测试成功

本轮之前 dispatch_diag 系列测试 (commit aa5accb0 之前, Nov 11) 全部成功运行。关键差异：

- **dispatch_diag 成功时**: 服务器已运行较长时间 (uptime > 24h)，URMA 子系统已稳定
- **本轮失败时**: 双端均刚重启 (202 uptime 8min, 204 uptime 13min)，URMA 子系统未完全恢复

此前 dispatch_diag 的 kernel_before.txt 记录的是 `ubcore_import_jetty_ex_old` (import jetty) 错误，但 bench.log 中无 bind_jetty 错误，测试正常完成。

本轮是 `ubcore_exchange_tp_info` (TP 交换) 错误，发生在 MAD 层，比 import jetty 更底层，直接阻止 jetty 绑定。

## 与 SPLIT_ERROR_REPORT 的关联

此错误与 2026-11-06 记录的 `SPLIT_ERROR_REPORT.md` 高度相似：

- 相同错误: `urma_bind_jetty failed: rc=4096` + `Failed to exchange tp info`
- 相同触发条件: 202 服务器重启后
- 相同无解: 模块重载、PCIe atomic 修复、GPU 唤醒均未能根治

当时结论: "202 服务器重启后 URMA 子系统未完全恢复，可能需要更深入的 URMA 驱动重置或联系系统管理员处理。"

## 结论

**根因**: 双端服务器刚重启 (uptime < 15min)，URMA 子系统的 MAD 层 TP 信息交换 (ubcore_exchange_tp_info) 持续失败，导致 jetty 绑定 (uburma_cmd_bind_jetty_ex) 失败，进程 SIGABRT。此问题在重启后持续存在，模块重载无法解决。

**此问题超出代码层面**，属于 URMA 驱动/固件在重启后未正确初始化的底层设备问题。此前成功的 dispatch_diag 测试运行在服务器长时间运行后，URMA 已稳定；本轮在重启后立即测试，触发了已知但未解决的 URMA MAD 初始化问题。

**建议**:
1. 等待服务器 URMA 子系统自行稳定 (可能需要更长时间，或多次重试)
2. 联系系统管理员检查 URMA 驱动/固件重启后初始化流程
3. 参考 SPLIT_ERROR_REPORT.md 中 "完整重载 udma + metax + bridge" 的修复尝试，但当时也未能根治
4. 如果 URMA 稳定后可重新运行本轮测试，代码和编译产物均已就绪

## 收集的文件

```
test/reference_events/results/
├── urma_ref_smoke128_01/
│   ├── node0/ (202)
│   │   ├── normal/bench.log (含完整崩溃日志)
│   │   ├── normal/status.json (rc=1, 所有 checks=false)
│   │   ├── normal/reference_validation_error.txt
│   │   ├── normal/runtime.json, phases.jsonl, api_samples.jsonl
│   │   ├── dmesg_master.txt (URMA MAD 错误)
│   │   ├── preflight.json, build_manifest.json, setup.json, host.json
│   │   └── lscpu.txt
│   ├── node1/ (204) — 对称失败，相同文件集
│   ├── build/ (build_manifest)
│   └── console/ (check.log, run.log)
├── urma_ref_build_base_01/ (编译成功日志)
└── urma_ref_build_trace_01/ (编译成功日志)
```

已保留所有日志、内核快照、状态文件，不包含 .so/objects。
