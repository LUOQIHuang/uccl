# 定位同内核 URMA Normal 剩余差距的 202 接收阶段

本轮已完成。无打点差距复现为 660.71 μs，GPU 打点使差额移动 872.08 μs。继续验证关键路径时使用 [204 第 6 块定点干预](../normal_chunk_gate/README.zh-CN.md)。 [本轮验收](results/normal_same_kernel_receive_01/summary/decision.json)

在同内核正式对照中，仅关闭 204 的 tail FADD fence 后，URMA 仍比 RDMA 慢 **619.42 μs（8.25%）**。54/54 个有效样本里，202 的 Dispatch 调用更慢。204 的第 6 块在 URMA 下，窗口满且前序第 2 条 WRITE 的 CQE 未可读的下界中位数为 **671.90 μs**。RDMA 分段里的同位置等待比 URMA 短，但 202 的探针扰动高于验收门槛，另外 RDMA 在末块的 FIFO 等待更长。关键问题是这些并行等待有没有推迟 202 的最终输出，以及晚在收到通知、数据确认还是转发输出。 [正式对照](results/normal_same_kernel_gap_01/summary/gap.json) · [URMA 完成记录分析](../normal_completion/results/normal_blockers_04/ANALYSIS.zh-CN.md) · [RDMA FIFO 控制组](../verbs_fifo/results/verbs_same_kernel_fifo_01/summary/controls.csv)

沿用两后端已构建的 `boundaries` 能力。中间一轮启用已有 GPU 测点及参考时钟，CPU 逐请求时间线和 FIFO 观测保持关闭。按 **202 每次最后结束的 Receiver 通道** 做分解，另外保留 12 个通道明细，用同一次调用的通道编号对应 204 的发送 GPU。所有时间间隔各端在本地算。等 tail 的最后阶段和取最后 token 在旧分析里落在同一个大区间，本轮分开统计。

| 202 最后结束通道的时间段 | 能区分什么 |
| --- | --- |
| 202 首次 WRITE 标记 → 该通道首次进入最后一批的 tail 检查 | 较早流水推进累积到了什么时间 |
| 首次进入最后一批的 tail 检查 → tail 可供继续读 | 最后一批的通知等待，包含期间调度其它轮询的时间 |
| tail 已可用 → 开始最后一个 token 的 epoch 检查 | 本通道消化最后一批中前面的 token 所花时间 |
| 最后 token 的 epoch 检查 → 确认有效 | 数据就绪检查及可能发生的等待 |
| 最后 token 已有效 → 该通道 Receiver 结束 | 接收端转发及输出收尾。Forwarder 结束时间单列 |

另保留 202 的 API 时延减去上述本地包络的有符号余量，并单列其中可观测的首 token 至首 WRITE 边界。`receiver_channels.csv` 的 204 第 6 块和末块发布是 GPU 发布标记。通道对应可以核对晚发布和晚完成是否落在同一通道。FIFO 队首 WR 到远端 token 的逐请求因果及设备内部时间仍需独立的同通道观测或干预。

## 两端运行

1. 两端进入仓库根目录并拉取本页所在提交。202 设 `RANK=0`，204 设 `RANK=1`，保持 `6.6.0-gdr_f00955680_diag`。设备、控制接口、已有构建目录与 [端到端对照](SAME_KERNEL_GAP.zh-CN.md) 一致。URMA 固定仅 204 tail-off，窗口 8、lane-mixed、ordered、completion all。RDMA 用纯 verbs + DMA-BUF。固定 BF16 4096 tokens/节点、chunk 32、5 预热和 10 采样，去掉索引 0。
2. 两端可独立预检。若已有模块在自定义位置，预检和正式运行均传相同的 `--verbs-build`、`--urma-build`。

   ```bash
   bash test/reference_events/run_same_kernel_gap.sh --study receive --action check --node-rank "$RANK"
   ```

3. 双机空闲时两端各运行一次。框架按 RDMA 的无采集、GPU 采集、无采集，再按 URMA 同样三轮。前后控制用于检验本次 GPU 采集是否改变原有差距。

   ```bash
   bash test/reference_events/run_same_kernel_gap.sh --study receive --action run --node-rank "$RANK"
   ```

   两端应看到 `SAME_KERNEL_RECEIVE_RUN_PASSED`。默认 master `141.61.17.202`，端口 `34280–34291`，JOB `normal_same_kernel_receive_01`。若调整 `--master-addr`、`--port`、`--job`，两端使用相同值。

## 回传与读取

回传两端 `test/reference_events/results/normal_same_kernel_receive_01/` 中的六轮和预检记录。保留两次采集的 `event_trace.jsonl`、六轮 `api_samples.jsonl`、`status.json`、构建和主机身份、benchmark 副本、GPU 快照及 `console`。异常时提交已有日志。

```bash
python3 tools/reference_events/summarize_receive_gap.py \
  --root test/reference_events/results/normal_same_kernel_receive_01 \
  --output test/reference_events/results/normal_same_kernel_receive_01/summary
```

先看 `controls.csv` 和 `decision.json`。两端和配对最大值的前后漂移、探针偏移、CV 都按 5% 门槛检查，同时检查两后端采集后的差额变化与时钟质量。采集造成的差额变化若已经与所要解释的某阶段差额同数量级，该阶段保留为线索。门槛通过后，使用 `receive_comparison.csv` 看哪段增加，再用 `latest_receiver.csv` 核对是否每次都集中在同一边界。若到最后 token 有效时差额已经形成，优先沿对应的发送及通知路径检查；若末 token 有效之后才形成，则转到 202 本地转发和输出。若最终输出通道和最后 token 通道不同，分别报告并行分支。
