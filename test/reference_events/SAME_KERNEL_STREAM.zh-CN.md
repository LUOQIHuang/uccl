# 用通信流计时定位 202 的剩余 Dispatch 差距

本轮已完成。四个事件将后端差额从 588.67 μs 移到 448.48 μs，变化 140.19 μs，超过 100 μs 门槛。采集中的 `cached_notify` 差 150.33 μs、主 Dispatch 差 301.71 μs，作为候选方向保留。下一轮 [202 全程等待对照](SAME_KERNEL_WAITS.zh-CN.md) 已去掉这四个事件。 [实测验收](results/normal_same_kernel_stream_01/summary/decision.json) · [分段数据](results/normal_same_kernel_stream_01/summary/comparison.csv)

下一项细分对照需要在同一次调用中，对 202 的接收通道分开记录全程累计的三种等待：远端 tail 未推进、tail 已推进但 token epoch 尚未匹配、本地转发缓冲空间不足。按同一通道查看等待和完成顺序，保留通道未覆盖或仍无法解释的时间。现有 `forwarder_incoming_tail_wait` 计时范围包括查找可消费来源的轮询，细分时应明确区分远端条件。启用记录后需同时复现无记录的后端差额，才能用这些时间定位剩余差距。 [源代码](../../ep/src/internode.cu) · [旧全范围计时的代表性失败](../dispatch_diag/results/urma_summary_01/ANALYSIS.zh-CN.md)

当前同内核的无打点对照中，URMA 仅 204 tail-off 比 RDMA 慢 **619–661 μs**，每次由 202 最晚结束。204 第 6 块额外等待约 302 μs 未显著拖慢 202。旧 GPU 时间线把后端差额移动了 872 μs。先在当前配置下确定余下时间主要落在主 Dispatch 通信流区间，还是准备、分配及调用交接区间。 [第 6 块结果](../normal_chunk_gate/results/normal_chunk6_gate_01/summary/decision.json) · [时间线扰动](results/normal_same_kernel_receive_01/summary/decision.json)

正式 BF16 计时使用已缓存的路由 handle，`cached_notify` 和主 Dispatch 都由同一次原生调用提交。只在每个后端的中间一轮、仅 **202** 的 BF16 采样，向现有通信流记录四个 CUDA Event。

| 时间区间 | 覆盖 |
| --- | --- |
| `cached_notify` 提交前 → 提交后 | 当前正式负载用于通知、更新元数据及准备的通信流区间 |
| `cached_notify` 后 → 主 `dispatch` 前 | 两次提交之间的流间隙 |
| 主 `dispatch` 提交前 → 提交后 | 主 Dispatch 在通信流上的执行区间，包含 kernel 自身等待 |
| 既有 API 时长 − 上述三个区间 | 其余时间的有符号余量，包含 API 的入口、分配、初始流依赖以及退出 |

四个事件在计时区间外预创建，沿用既有同步和正确性检查。无需启动 GPU 时间线、设备映射时钟或 CPU 逐请求采集。调用流和通信流可以交叠，所以余量保留正负值。通信流事件直接夹住两个原生提交，仍可能含微小的事件入队间隔。若 `dispatch_comm_us` 承载差距，再拆其内部。

## 两端运行

两端更新到本文档所在提交并进入仓库根目录。保持 `6.6.0-gdr_f00955680_diag`。202 设 `RANK=0`，204 设 `RANK=1`。两端可分别提前执行 `setup`。脚本在独立源码树构建纯 verbs 和 URMA 的诊断模块，并预检两套扩展。现有基线模块仍可继续使用。

```bash
bash test/reference_events/run_same_kernel_gap.sh --study stream --action setup --node-rank "$RANK"
```

两端都看到 `SAME_KERNEL_STREAM_SETUP_PASSED` 后，在一个空闲双机窗口各执行一次。

```bash
bash test/reference_events/run_same_kernel_gap.sh --study stream --action run --node-rank "$RANK"
```

固定 Normal BF16 Dispatch，每节点 4096 tokens、chunk 32。URMA 仅 204 tail-off。RDMA 和 URMA 各跑 `pre → probe → post`，每轮 5 预热、10 采样，汇总保留后 9 条。默认 `normal_same_kernel_stream_01`、master `141.61.17.202`、端口 `34320–34331`。两端看到 `SAME_KERNEL_STREAM_RUN_PASSED` 即可。默认源码树为 `/home/h00918771/metax_dev/normal_stream_src_v1`，两个模块目录在 `/home/h00918771/metax_dev/normal_stream_build/` 下。需要自定义路径时，准备和运行使用相同的 `--stream-tree`、`--verbs-build`、`--urma-build`。

回传双方 `test/reference_events/results/normal_same_kernel_stream_01/` 的内容。保留原始 `api_samples.jsonl`、202 两轮 probe 的 `stream_timing.jsonl`、控制记录、`overlay_node*.json`、模块和主机身份、benchmark 副本、GPU 快照及 `console`。

```bash
python3 tools/reference_events/summarize_stream_timing.py \
  --root test/reference_events/results/normal_same_kernel_stream_01 \
  --output test/reference_events/results/normal_same_kernel_stream_01/summary
```

先读 `decision.json`。除前后控制的单端及配对 CV、漂移/扰动均须在 5% 内，本轮还要求打点前后后端差额移动不超过 **100 μs**，约为待解释差距的六分之一。满足后再看 `comparison.csv` 的准备区间、主调用区间和主调用以外的差额，逐样本时序保留在 `stream_windows.csv`。如果打点仍明显移动差额，保留数据并停止用这套观测量化差距。
