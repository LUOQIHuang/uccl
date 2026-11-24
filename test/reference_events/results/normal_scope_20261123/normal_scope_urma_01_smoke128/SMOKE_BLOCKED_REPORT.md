# NORMAL_REVIEW_RUN Smoke 测试阻塞报告

## 测试文档
- 文档: `test/reference_events/NORMAL_REVIEW_RUN.zh-CN.md`
- 提交: `61a1e6fa53d8071745e8e7373c0fb42a8680c476`
- 后端: URMA (kernel `6.6.0-gdr_f00955680_diag`)
- Fence: `peer-tail-off`
- 两端: 202 (RANK=0) + 204 (RANK=1)

## 环境修复（已完成）

两端拉取最新代码至 `61a1e6fa`，204 原为 `49fd92a6`。

### PCI/设备修复
| 项目 | 202 (RANK=0) | 204 (RANK=1) |
|------|--------------|--------------|
| GPU PCI | `0000:21:00.0` | `0000:21:00.0` |
| 网卡(NIC) PCI | `a1:00.0` (Mellanox CX-6) | `28:00.0` (Mellanox CX-6) |
| Atomic bridge | `a0:08.0` → setpci `0060` | `a0:08.0` → setpci `0060` (also `20:10.0`→`0060`) |
| 设备权限 | `chmod 666 /dev/mxcd /dev/dri/renderD128` | 同 |

### 网络接口名称变化（关键发现）
- **历史成功运行** (urma_normal_tail_order_01_smoke_tail_interleaved) 使用 `NCCL_SOCKET_IFNAME=enp33s0`
  - NetworkManager 显示 `enp33s0` 是旧连接名，映射到当前设备 `enp40s0` (202)
  - 204 之前也有 `enp33s0`，现已更名为 `enp34s0f5u1u4`
- **当前 OOB 接口**: 202=`enp40s0` (141.61.17.202), 204=`enp34s0f5u1u4` (141.61.17.204)
- **RoCE NIC 接口**: 202=`enp161s0f0np0` (192.168.17.202), 204=`enp40s0f0np0` (192.168.17.204)
- NIC-to-NIC ping 双向正常 (0.04-0.08ms, 0% loss)

### driver.py 修改（两端一致）
1. `gpu-pci` 默认值: `0000:a1:00.0` → `0000:21:00.0` (a1 是 NIC 不是 GPU; 204 上 a1 不存在)
2. `environment()` 函数: 保留 `UCCL_SOCKET_IFNAME` 环境变量 (原代码会删除所有 `UCCL_*` 前缀变量)
3. `environment()` 函数: `NCCL/MCCL_SOCKET_IFNAME` 使用 `UCCL_SOCKET_IFNAME` 覆盖 `args.ifname`
   - 202 设置 `UCCL_SOCKET_IFNAME=enp40s0`
   - 204 设置 `UCCL_SOCKET_IFNAME=enp34s0f5u1u4`

两端 driver.py SHA256 一致: `50af9b4c8391c81d9e1285b49234a0d7`

## 构建结果
- 两端构建通过 (`BUILD_PASSED` / `BUILD_ALREADY_PASSED`)
- 模块: `/home/h00918771/metax_dev/normal_scope_build/urma_v1/lib/ep.abi3.so`
- MODE_PARITY_CHECK_PASSED (diagnostics_hash 两端一致)

## Smoke 测试结果：BLOCKED

### 进展阶段
1. ✅ Preflight (kernel/device/setpci/runpm/p2p/journal 全通过)
2. ✅ MODE_PARITY_CHECK_PASSED (源码/诊断哈希一致)
3. ✅ NCCL bootstrap (enp40s0/enp34s0f5u1u4, Channel 00-31 建立)
4. ✅ TRANSPORT_MODE_OK (URMA proxies=4 初始化)
5. ✅ layout 阶段 (单节点, ~132μs)
6. ❌ **dispatch_fp8_chunk32 阶段: `RuntimeError: DeepEP error: timeout (dispatch CPU)`**

### 错误详情
```
File "ep/bench/buffer.py", line 1661, in internode_dispatch
    ) = self.runtime.internode_prepare(
RuntimeError: DeepEP error: timeout (dispatch CPU)
[wait_until_cmd_consumed nvl:0 cmd:4 label:-1] waiting slot=1
```

- 202 (master, rank0) 在 `internode_prepare` 中等待 204 的 URMA 响应超时
- 204 (worker1, rank1) 已正常退出 (exit code -6 SIGABRT)
- 202 的 worker.py 进程挂起 (CPU 617%, 39min+), 需手动 kill
- GPU 显示 1300MiB 被占用 (stuck process)

### 已排除的原因
- ❌ NCCL 初始化失败 → NCCL bootstrap 成功, Channel 全部建立
- ❌ 网络不通 → NIC-to-NIC ping 正常
- ❌ URMA 设备缺失 → 设备存在, 内核模块全部加载
- ❌ GPU 占用 → 清理后 GPU idle, 但每次 dispatch 阶段仍超时
- ❌ 源码不一致 → diagnostics_hash 两端匹配, PAIR_STOP setup 已解决
- ❌ Barrier 残留 → 已清理 /dev/shm/uccl_barrier_*
- ❌ setpci 未设置 → 两端 a0:08.0 均为 0060
- ❌ GPU kill queue/ringbuf 错误 → journalctl 检查为 0

### 可能原因（未确认）
1. **GPU RAS 错误**: 202 journal 显示 `LQC.DLPHY.MUXPCS` RAS 错误 (Nov 24 19:29-23:18), 可能影响 GPU↔URMA P2P 通路
2. **URMA 设备状态**: 多次失败运行后 URMA 设备可能需要重置 (但无明确错误日志)
3. **网络接口名称变化**: `enp33s0` → `enp40s0`/`enp34s0f5u1u4`, 可能影响 UCCL proxy 的 OOB 连接路径 (虽然 NCCL bootstrap 成功)
4. **时序问题**: 204 启动延迟可能导致 202 dispatch 超时 (但之前成功运行也使用相同启动顺序)

### 历史成功运行对比
| 项目 | 历史 (smoke_tail_interleaved) | 当前 (smoke128) |
|------|------------------------------|-----------------|
| gpu_pci | a1:00.0 (NIC, 误用但通过) | 21:00.0 (GPU, 正确) |
| ifname | enp33s0 (当时存在) | enp40s0/enp34s0f5u1u4 (当前) |
| NCCL bootstrap | enp33s0:141.61.17.x | enp40s0:141.61.17.202 |
| submit_schedule | tail-interleaved | lane-mixed (默认) |
| dispatch_fp8 | ✅ 1480μs | ❌ timeout |
| boot_id | b45139e1... | (未检查是否相同 boot) |

## 已修改的文件
- `/home/h00918771/metax_dev/normal_scope_urma_src_v1/test/urma_mode_parity/driver.py` (两端, worktree 内, 未提交)
  - gpu-pci default: `0000:21:00.0`
  - environment(): 保留 UCCL_SOCKET_IFNAME, NCCL/MCCL/UCCL_SOCKET_IFNAME 使用 _iface
- `/home/h00918771/normal_review_env.sh` (两端, 未提交)
  - 设置 UCCL_SOCKET_IFNAME 按节点区分
- `/home/h00918771/normal_review_build.sh` (两端)
- `/home/h00918771/normal_review_run.sh` (两端)

## 建议
1. 尝试重启 GPU 驱动 (`sudo rmmod metax && sudo modprobe metax`) 或重启机器清除 RAS 错误状态
2. 检查 URMA 设备是否需要 `ubctl` 重置
3. 对比当前 boot_id 与历史成功运行的 boot_id, 确认是否同一 boot session
4. 尝试使用 verbs 后端 (需切换内核) 验证是否为 URMA 特有问题
