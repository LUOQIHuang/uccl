# CPU Proxy 完整流程（当前 URMA 分支）

分析基线：`fe0959a2`。路径均相对仓库根目录。本说明针对 `ep.Proxy / UcclProxy / Proxy::run_dual` 的真实 EP 路径；`FifoProxy` 是另一套 benchmark 包装，不是此处 URMA 主路径。未来修改代码后应重新核对行号和语义。

## 1. 分层和状态所有者

```
Python Buffer / initialize_uccl
    → GPU Dispatch / Combine
    → uccl_ibgda.cuh：构造 TransferCmd
    → D2HHandle → MSCCL++ FIFO（主机映射内存）
    → UcclProxy 的 std::thread
    → Proxy::run_dual
    → UrmaTransport（每个需要连接的 peer 一个）
    → URMA Jetty / JFC → 网络 → 远端内存
```

默认每 rank 四线程，每线程八 FIFO。每线程维护独立的 pending、acked、在途操作总数；同一 rank 的线程共享主数据 buffer 和 atomic buffer。跨节点 Normal 模式只连接相同节点内 GPU 序号的 peer；LL 模式连接所有跨节点 peer，同节点数据另走 GPU IPC。

核心文件：

| 文件 | 职责 |
|---|---|
| `ep/bench/buffer.py`, `ep/bench/utils.py` | 创建 buffer、识别拓扑、创建 Proxy、交换元数据、启停 |
| `ep/src/uccl_proxy.cpp` | 每个 Proxy 的 FIFO、线程、atomic buffer 生命周期 |
| `ep/include/uccl_ibgda.cuh` | GPU 的 WRITE/ATOMIC/QUIET/BARRIER 接口 |
| `ep/include/d2h_queue_device.cuh`, `d2h_queue_host.hpp` | 128-bit 命令打包、解包 |
| `ep/include/fifo_device.hpp`, `ep/src/fifo.cpp` | GPU 发布和 CPU 消费协议 |
| `ep/src/proxy.cpp` | 调度、流控、URMA 分发、GPU 应答、屏障 |
| `ep/src/urma_transport.cpp` | URMA 资源、注册、提交、完成回收、销毁 |

## 2. 创建和初始化

1. Python 创建 scratch/RDMA tensor、Buffer，识别 device ordinal、NIC local rank、barrier local rank。三者职责不同，不能混用。
2. `initialize_uccl` 创建四个 `UcclProxy`，每个构造八个 FIFO。线程 0 分配 atomic buffer，再把指针设置到其余 Proxy。
3. Python 收集所有 rank 的 IP、端口、buffer 地址等元数据，调用 `set_peers_meta`；注册 GPU 所需的 D2H handles。
4. 多机运行时启动 `start_dual()`。`UcclProxy::start` 启动 OS 线程，调用 `Proxy::run_dual()`。
5. `init_common` 检查 `UCCL_EP_TRANSPORT`。请求 URMA 但未启用 `USE_URMA` 会报错；当前要求 RC，拒绝 HIP 非 MetaX 路径和 subset barrier。
6. 每个连接 peer 创建 `UrmaTransport`：进程级引用计数管理 `urma_init/uninit`；按 device/EID 共享 context；分别创建 JFCE、JFC、JFR 和 Jetty。每个 RC Jetty 只绑定一个 peer。
7. 注册主 GPU 数据段、atomic 控制段、FADD old-value scratch、主机 barrier words。相同 context/范围/权限的 segment 复用；有冲突的 GPU 页重叠注册被拒绝。
8. TCP 交换连接描述符，检查 wire version/原生结构大小/权限，import jetty 和三个远端 segment，然后 bind。双方必须使用兼容 SDK/ABI。
9. Normal 模式按节点和 thread index 建立共享内存 LocalBarrier，记录各 local rank 的 arrive/release 序号。
10. 初始化每个 FIFO 的序号和 pending deque，进入主循环。

注意：URMA 分支在 `init_common` 内提前 return，未走后面 verbs 的 `pin_thread_to_numa_wrapper()`。当前默认 NIC 选择是跳过 bonding 后按 NIC local rank 轮转，不是 PCIe/NUMA 距离选择。主机 FIFO 分配有 GPU NUMA preferred 策略，但这不等同于 Proxy 线程已经绑核。

NVIDIA URMA atomic buffer 为 host-mapped 类型；MetaX 生产路径使用 fine-grained device allocation。主数据 buffer 为 GPU 内存，URMA 注册失败不会自动退回 host staging。CPU barrier words 和 FADD old-value scratch 仍在主机内存。

## 3. GPU 如何发布命令

`TransferCmd` 为 16 字节，包含类型、目标 rank、长度/通知值、压缩地址偏移和 expert/atomic offset。Normal/LL 的 WRITE 地址解码步长不同；ATOMIC 的 value 是有符号值，不能按普通 WRITE 地址解释。

WRITE 由 warp lane 0 发布，按 expert 编号映射 Proxy/FIFO；ATOMIC 按 warp 编号映射。多 GPU producer 通过 device head 的原子加法领取槽位。槽位满时读取 CPU tail 等待。

FIFO triggers/tail 位于主机映射内存，head/tailCache 位于设备内存。GPU 先保证数据和命令的发布顺序，再发布非零 fst；CPU acquire-load fst 后读取 snd。CUDA A100 与其他 CUDA/MetaX 分支使用不同的 fence/store 实现。不要仅依据函数名字就删掉 system-scope 排序。

## 4. 主循环和背压

`run_dual` 每轮依次执行：

```
maybe_sleep
→ poll_urma_completions
→ notify_gpu_completion
→ post_gpu_command → post_gpu_commands_mixed → post_urma_commands
→ Normal: barrier_check
```

默认忙轮询。Adaptive sleeper 是长时间无活动后的可选休眠，不是每条消息的等待方式。

`post_gpu_command` 从固定编号的 FIFO 依次收集命令。每 FIFO pending 限额为 Normal 8、LL 32，可由 `UCCL_IB_MAX_INFLIGHT_NORMAL/LOW_LATENCY` 调节；另按每线程字节数限制，阈值为 `UCCL_IB_MAX_INFLIGHT_BYTES / 4`。普通命令复制到 CPU vector 后立即 `fifo->pop()`，QUIET/BARRIER 保留在 FIFO 头部直到完成。

每条命令 ID = FIFO index 高 32 位 + FIFO 序号低 32 位。pending deque 保存退休顺序。不同 FIFO 可并行推进，同一 FIFO 前面的命令未退休会阻止后续命令的额度回收。

URMA 另有每 Proxy 的总在途操作计数，上限 `min(kMaxOutstandingSends=2048, kMaxAtomicOps=1024)`。超过额度时同步轮询完成，最长 30 秒。这里限制的是网络操作数；一条 Normal WRITE 可能使用两个网络操作。

## 5. WRITE / ATOMIC 提交

收集到的命令分两遍处理：先 WRITE/ATOMIC，再 QUIET/BARRIER，确保本轮其他 FIFO 已收集的数据先于控制命令处理。

### WRITE

1. 根据 Normal/LL 解码 local/remote offset，查找 peer transport。
2. 检查 segment 范围，创建 OpContext 和源/目的 SGE。
3. 提交 `URMA_OPC_WRITE`，strong order，开启完成事件。
4. Normal 且通知 offset/value 有效时，再提交 fenced FADD。WRITE 使用内部 sentinel ID，通知 FADD 承接原命令 ID。两个操作都计入总在途数。
5. 这是 CPU 批量收集、URMA 逐条 post；当前没有 WR 链批量提交。

### ATOMIC

1. 对命令值做已有协议映射：large value 特殊值、combine 增量等。
2. 检查远端 offset 范围和八字节对齐。
3. 从 1024 个 old-value 槽中线性寻找空闲槽，分配 OpContext。
4. 提交 `URMA_OPC_FADD`，strong order、fence、完成事件。
5. 完成后才能复用 old-value 槽。业务接口是 non-fetch add，返回旧值只是底层 FADD 的要求。

## 6. 完成回收与 GPU 可见进度

`poll_urma_completions` 依次遍历各 peer。每个 transport 单次最多取 16 个 CR。CR.user_ctx 指向 OpContext，当前通过链表查找校验，再释放 atomic operand slot、删除节点、转换为 TransportCompletion。非成功状态抛错；普通 ID 放入 `acked_wrs_`，内部 ID 只减少在途计数。

下一次 `notify_gpu_completion` 按各 FIFO pending 顺序退休已 ack 的条目，回收字节额度。QUIET/BARRIER 条目退休时才释放保留的 FIFO 槽位，让 GPU 的 `wait_until_cmd_consumed` 退出。

必须区分：

| 状态 | 说明 |
|---|---|
| FIFO 已消费 | CPU 已复制命令，普通命令可能尚未 post |
| URMA 操作完成 | 驱动报告该操作完成，CPU 可按协议回收资源 |
| 远端 GPU 已处理 | 由远端 GPU 读取 flag/count/payload 的协议决定，不能仅由本地 CE 推断 |

## 7. QUIET 的完整路径

1. GPU 向所有 32 个 FIFO 各发布 QUIET，再等待全部标记被消费。
2. 每 Proxy 只有一个 `quiet_inflight/quiet_wr`，同一时刻只接受一个 QUIET；其余 FIFO 遇到 QUIET 暂停。
3. CPU 先提交本轮收集的其他数据，再用 `ensure_urma_capacity(limit)` 等待总在途数归零。
4. 将 QUIET ID 放入完成集合，下一轮按 pending 顺序退休，pop 对应 FIFO。
5. 继续处理其他 FIFO 的 QUIET。因此八个 FIFO 的控制标记逐个经过调度，并不代表每次都有新的 DMA 需要等待。

优化不能简单删除兄弟 FIFO 标记：尚未取到 CPU 的旧命令不在 URMA 在途计数中。可设计各 FIFO 水位/epoch，把已覆盖标记一起完成，但必须保持每个 FIFO 的边界和 buffer 复用约束。

## 8. BARRIER 的完整路径

GPU helper 实际只向 Proxy 0 的第一个 FIFO 发布 BARRIER（循环中有明确 break）。它不是四个 Proxy 的全局 drain，完整数据排空需由调用点的 QUIET 等协议保证。

1. Proxy 排空自身已有网络操作，增加 barrier sequence，在主机共享内存写本 local rank 的 arrive_seq。
2. 节点 leader 等待所有 local rank 到达同一序号。
3. 非根节点 leader 向 rank 0 的 arrival word FADD +1。
4. rank 0 汇总各 transport 的 arrival word，等待累计值达到 `seq * (num_nodes - 1)`。
5. rank 0 向各远端节点 leader 的 release word FADD +1，并发布本节点各 rank 的 release_seq。
6. 远端 leader 看到 release word 达到 seq 后，发布自己节点的 release_seq；非 leader 读取共享内存等待释放。
7. 对 BARRIER ID 应答，退休时 pop FIFO，GPU 继续执行。

当前是节点分层＋节点间根节点扇入/扇出；大规模下 root 消息数和等待最慢节点可能影响尾延迟。单个 FADD 微基准不能代表完整屏障耗时。

## 9. 停止、异常与生命周期

Python destroy 停止 Proxy；`stop` 清除 progress_run，join 工作线程，再销毁 transport。保留 tensor 引用时，allocation lease 防止 scratch 过早释放。

URMA destroy 顺序：drain 未完成操作 → unbind → unimport 远端段/jetty → delete 本地 Jetty/JFR/JFC/JFCE → 清除注册引用（先 unregister segment，再释放 token）→ 释放 context → 最终 allocation lease 释放 GPU/host 内存 → 配对 uninit。

提交失败释放当前操作上下文及 operand slot；完成错误进入失败状态；销毁阶段若无法安全排空或解除注册，代码采用 terminate，避免 DMA 访问已释放内存。Proxy 工作线程异常也应结合 stderr 检查，不能只看 Python 最后一行。

## 10. 优化边界与观测点

首轮优先：URMA 绑核/拓扑、OpContext 对象池、operand freelist、完成表、按 peer 批量提交、活跃 JFC 轮询。再做 QUIET epoch 合并。减少 CE/fence、替换 FADD、屏障算法变化放在后面。

建议按线程统计：FIFO 空轮询/命令数/批大小、每 peer WR/CE、post/poll 时间、pending 高水位、字节与操作背压等待、QUIET 次数及等待、barrier local/root/release 各段耗时。当前提供的 EP 基线运行真实生产路径，但尚未在热循环插入这些新计数器；避免把微基准结果误称为 Proxy 分阶段 profiling。
