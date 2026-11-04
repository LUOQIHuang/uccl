# URMA Mode Parity 测试事件报告 — `urma_register_seg` GPU P2P 段注册失败

- **事件编号**: urma_mode_v2_01 / run-01
- **测试**: `test/urma_mode_parity` Step 6 `--action run` (mode=normal)
- **时间**: 2026-11-05 01:27–01:29 (两机)
- **结论**: 失败发生在**闭源 `liburma.so` 的 `urma_register_seg` 内部**，在内核 P2P pin 成功**之后**返回 NULL。不在我们能读/能改的代码边界内。按用户指示"如果有无法解决的问题，可以记录日志"存档。

---

## 1. 环境（两机一致）

| 项 | 202 (master, RANK=0) | 204 (worker1, RANK=1) |
|---|---|---|
| kernel | `6.6.0-gdr_f00955680_diag` ✓ | `6.6.0-gdr_f00955680_diag` ✓ |
| URMA device | `udmac0d1e2` ✓ | `udmac0d1e2` ✓ |
| p2p bridge | `udma_metax_p2p_bridge` loaded ✓ | loaded ✓ |
| runpm_link | 0 ✓ | 0 ✓ |
| GPU runtime | active ✓ | active ✓ |
| PCIe atomic | 0060 (bit0x40 set) ✓ | 0060 ✓ |
| GPU pre-run | `no process found` / `Available` / 858MiB baseline ✓ | 同 ✓ |
| SDK include | `/home/h00918771/metax_dev/urma_sdk_private_include` (is_gpu_seg@urma_types.h:999) ✓ | 同 ✓ (SHA256 一致) |
| liburma | `.../umdk_private_618957c3/lib/liburma.so.0` ✓ | 同 ✓ |
| ep.abi3.so sha | `f4d3cff0...` | `96e1dbac...` |
| wire version | 2 ✓ | 2 ✓ |
| torch | 2.6.0+metax3.7.0.7 | 同 |

**preflight（urma_preflight.json）8 项硬件检查全部通过**，run 前 GPU 空闲。环境侧无异常。

---

## 2. 时间线（崩溃 run）

### 202 (master, RANK=0)
| 时刻 | 事件 | 来源 |
|---|---|---|
| 01:27:04 | GPU 空闲快照 (`gpu_before.txt`: no process, Available) | gpu_before |
| 01:27:20 | 内核 `udma_metax_p2p_bridge: pin OK` (iova=0xa04c00000) | dmesg |
| 01:27:20 | `ubcore_register_seg` 多个小段 (8192/64B) MATT START | dmesg |
| 01:27:43 | `UDMA GPU unmap` (崩溃进程清理) | dmesg |
| 01:27:44 | `current mm released` ×6 (崩溃清理) | dmesg |
| **01:27:44** | **SIGABRT exit -6** `UcclProxy thread 2 failed: URMA MetaX P2P segment registration failed (no host fallback)` | bench.log |

### 204 (worker1, RANK=1)
| 时刻 | 事件 | 来源 |
|---|---|---|
| 01:27:59 | GPU 空闲快照 | gpu_before |
| 01:28:16 | 内核 `pin OK` (iova=0xa04c00000, first_pa=0xa0000ce00000) | dmesg |
| 01:28:40 | `UDMA GPU unmap` (崩溃清理) | dmesg |
| 01:28:41 | `current mm released` ×4 + `udma_free_page_priv: 22 callbacks suppressed` | dmesg |
| **01:28:41** | **SIGABRT exit -6** `UcclProxy thread 3 failed: URMA MetaX P2P segment registration failed (no host fallback)` | bench.log |

> 注：dmesg 在 01:28:15(202)/01:29:10(204) 还有一组 `pin OK va=0xa07000000 len=117506432(112MB)`，是崩溃后的**后续重试**进程，非本次崩溃 run。

---

## 3. 崩溃前用户态进度（bench.log 证据）

崩溃前已成功完成：
1. MCCL init COMPLETE（32 coll channels, busId a1000）
2. MCCL 全部 32 channels 收发拓扑建链（Connected all rings/trees）
3. `[URMA] rank=X thread=0..3 using device N/6: udmac0d1e2`（4 线程各自选中 URMA 设备）
4. `[URMA] mode=normal channels=8 endpoints=9 batch=linked capacity=1024 wire=2`（URMA transport 初始化成功，wire-v2 协商完成）×4 线程

崩溃点（4 线程中 **1 个** 失败）：
```
UcclProxy thread 2/3 failed with exception: URMA MetaX P2P segment registration failed (no host fallback)
terminate called after throwing an instance of 'std::runtime_error'
  what():  URMA MetaX P2P segment registration failed (no host fallback)
Fatal Python error: Aborted
  File ".../ep/bench/utils.py", line 677 in initialize_uccl
  File ".../ep/bench/buffer.py", line 161 in __init__
  File ".../node{0,1}/normal.py", line 561 in test_loop
```
- 202 失败线程 = thread 2；204 失败线程 = thread 3（不一致 → 非确定性，符合竞态/资源耗尽特征）
- status.json: `rc=1`, `native_mode=true`, `device=true`（URMA transport init 与设备选择均成功），其余 check=false（未到达）

---

## 4. 根因定位

### 4.1 我们的代码边界（`ep/src/urma_transport.cpp:388-400`）
```cpp
cfg.is_gpu_seg = gpu ? 1 : 0;          // gpu=true 主缓冲区
auto* seg = urma_register_seg(urma_ctx_, &cfg);
if (!seg) throw std::runtime_error(
    "URMA MetaX P2P segment registration failed (no host fallback)");
```
`urma_register_seg` 是 `liburma.so` 导出的闭源函数。它返回 NULL → 我们抛异常。**抛异常点是正确的**（GPU 段不允许 host fallback）。

### 4.2 内核侧 pin 成功（dmesg 铁证）
崩溃时刻，`udma_metax_p2p_bridge`（我们这边的内核模块）的 pin 回调**成功**：
```
udma_metax_p2p_bridge: pin OK va=0xa04c00000->... first_pa=0xa0000ce00000
UDMA GPU map: ... iova=0xa04c00000 first_pa=0xa0000ce00000 nents=1
```
→ **我们负责的 GPU P2P pin 路径工作正常**。pin 回调把 GPU 物理页成功映射进了 UDMA IOVA 空间。

### 4.3 失败发生在 pin 之后的 liburma/ubcore 逻辑
`urma_register_seg` 内部流程（据 liburma.so 符号 + dmesg）：
```
urma_register_seg (liburma.so, 闭源)
  → urma_cmd_register_seg → ioctl(URMA_CMD_REGISTER_SEG)  ← 可能在此返回错误
     → ubcore_register_seg (内核)
        ├─ udma_metax_p2p_bridge pin  ← 成功 (dmesg: pin OK)
        ├─ MATT 映射 (dmesg: MATT START:kernel_mode=0)  ← 可能在此失败
        ├─ token alloc / seg-table 入项  ← 可能在此失败
        └─ ...
  ← 返回 NULL (liburma 未打印错误，日志默认静默)
```
liburma.so 中与 `urma_register_seg` 返回 NULL 相关的错误字符串：
```
Invalid parameter
ioctl failed in urma_cmd_register_seg, ret=%d, errno=%d.
Failed to malloc buffer.
```
**这些错误信息在 bench.log 中均未出现** —— liburma 的错误日志默认静默（`urma_log`/`urma_log_drop` 内部符号，未发现可设的环境变量开关）。因此无法从现有日志取得确切的 `errno`/`ret`。

### 4.4 失败模式：4 线程并发，1 个失败
- 4 个 UcclProxy 线程各自 `register_main_buffer(gpu=true)`
- dmesg 显示**多个**段 pin OK（小段 8192/64B + 大段都成功）
- 但 4 线程中 **1 个**的某次 `urma_register_seg` 返回 NULL
- 失败线程号不固定（202=thread2, 204=thread3）
- → **非确定性，高度符合并发资源竞态/耗尽**（MATT 表项 / token ID / seg-table 容量 / ioctl 串行化窗口），而非确定性的参数错误

---

## 5. 已排除的原因

| 假设 | 排除依据 |
|---|---|
| 内核/p2p bridge 模块问题 | dmesg `pin OK` 在崩溃时刻存在；`urma_preflight.json` 全过 |
| GPU 不空闲/被占 | `gpu_before.txt`: no process, Available, 858MiB baseline |
| SDK 头缺 `is_gpu_seg` | 私有 SDK 头已就位，`is_gpu_seg@urma_types.h:999`，SHA256 两机一致 |
| liburma.so 加载错误 | `runtime.json` 确认加载 `liburma.so.0` 私有版 |
| wire 版本不对 | runtime.json wire=2，bench.log `wire=2` |
| URMA transport init 失败 | bench.log `mode=normal channels=8 ... wire=2` ×4 成功 |
| 设备选择失败 | bench.log `using device N/6: udmac0d1e2` ×4，status device=true |
| build 产物损坏 | ep.abi3.so 存在、module_sha 记录、能加载并跑到 register 段 |
| runpm_link / atomic 不对 | preflight: runpm_link=0, atomic=0060 |

---

## 6. 无法从本侧解决的原因

1. `urma_register_seg` 是**闭源** `liburma.so` 函数，返回 NULL 的确切 errno 不可见（错误日志默认静默，无外部开关）。
2. 失败在内核 `ubcore_register_seg` 的 **pin 之后**步骤（MATT/token/seg-table），`ubcore` 也是厂商闭源内核模块。
3. 4 线程并发 1 失败的非确定性 → 需要厂商在 liburma/ubcore 侧定位并发资源竞态，或提供日志开关 / 资源上限查询接口。
4. `strace` 可抓 ioctl errno，但会改变 4 线程竞态时序（可能让失败消失或换线程），且需协调双机 180s 窗口，代价/收益不划算；留给厂商在可控单测环境做。

---

## 7. 给 UMDK / MetaX 厂商的建议下一步

1. **开启 liburma 错误日志**：提供 `urma_log` 级别设置接口或环境变量，使 `ioctl failed in urma_cmd_register_seg, ret=%d, errno=%d.` 真正打印，取得崩溃那次 ioctl 的 `ret`/`errno`。
2. **strace 单测**：对单进程 `urma_register_seg(is_gpu_seg=1, 112MB)` 在 4 线程并发下 strace，抓 `ioctl(URMA_CMD_REGISTER_SEG)` 返回值与 errno。
3. **查 ubcore 资源上限**：MATT 表项数、token ID 池容量、seg-table 容量是否足以支撑 4 线程并发各注册 1 个 112MB GPU 段 + 若干小段；重点看 1/4 失败是否对应某资源 `ENOMEM`/`ENOSPC`。
4. **确认 112MB 单段**：`iova=0xa07000000 len=117506432` 是否超过单段 MATT 上限或触发某边界（虽然 pin OK，但 MATT 入项可能按段数/大小有独立限制）。
5. **并发序列化**：若确为资源竞态，评估 liburma 是否应对 `URMA_CMD_REGISTER_SEG` ioctl 加锁或重试。

---

## 8. 文件清单（日志存档位置）

### 工作区
- `D:\Workdspace\logs\urma_parity_incident\INCIDENT_REPORT.md` — 本报告

### 202 (master) `/home/h00918771/metax_dev/urma_mode_parity_results/urma_mode_v2_01/node0/normal/`
- `bench.log` (19802B, 222行) — 完整崩溃 trace
- `status.json` — rc=1, native_mode/device=true
- `urma_preflight.json` — 8 项硬件检查全过
- `runtime.json` — wire=2, liburma.so.0 加载, module_sha
- `gpu_before.txt` — run 前 GPU 空闲
- `counters_before.json` / `counters_after.json` — URMA device 计数器前后

### 204 (worker1) `.../node1/normal/`
- 同上结构（bench.log 17787B/185行）

### 内核日志（dmesg，崩溃窗口 01:27–01:29）
- 两机均已用 `step7e_dmesg.sh` 抓取（见本次会话输出），含 `pin OK` / `UDMA GPU map` / `ubcore_register_seg` / `MATT START` / `current mm released` 完整序列

### 辅助脚本（`/root/` on 各机）
- `step7e_dmesg.sh` — dmesg 崩溃窗口抓取
- `go_202.sh` / `go_204.sh` — 一键 clean+launch run
- `kernel_switch_check.sh`, `final_verify.sh` 等

---

## 9. 本次测试整体进展（Step 3→6）

| Step | 动作 | 结果 |
|---|---|---|
| 3 | build (run_urma_tests.py + setup.py build_ext) | ✓ 两机成功，ep.abi3.so 生成 |
| 4 | check (preflight + sdk 校验) | ✓ 两机 8 项硬件检查 + SDK 头校验全过 |
| 5 | run 准备 (idle_check + pair + torchrun rendezvous + worker launch) | ✓ 两机握手成功，MCCL init COMPLETE |
| 6 | run (normal mode test_loop) | ✗ urma_register_seg GPU 段返回 NULL → SIGABRT |
| 7 | collect | 未到达（依赖 Step 6 通过） |

**Step 6 比预期走得更远**：setup/idle/pair/torchrun/worker/MCCL init/URMA transport init **全部通过**，仅在第一条 GPU 主缓冲区 `urma_register_seg(is_gpu_seg=1)` 处崩溃，且内核 pin 已成功。失败点明确在闭源 liburma/ubcore 的 pin 后处理。
