# 202 最晚完成通道的全程等待对照

本轮回答剩余约 **620–660 μs** 是否集中在接收通道的一类等待。前次较粗的通信流计时并入本轮，用来确认差额是不是落在主 Dispatch。旧 GPU 时间线和 204 第 6 块干预的结果已经归档。 [正式差额](results/normal_same_kernel_receive_01/summary/decision.json) · [204 干预](../normal_chunk_gate/results/normal_chunk6_gate_01/summary/decision.json)

只在 **202** 启用采集。对每个接收通道，用同一 Forwarder warp 的 `clock64` 累计全程等待，用接收输出结束的登记顺序选择每次最后完成的通道。完成顺序每通道登记一次。计数在 Forwarder 结束时一次写入显存，计时结束后由主机读取。没有 CPU 时钟更新线程、主机映射时间戳或 CPU 逐请求时间线。

| 本轮区分的等待 | 实际边界 |
| --- | --- |
| 远端 tail | 在只有远端 token 尚未处理时，从查找可消费来源到看到远端 tail 推进。包括成功检查本身 |
| 远端 token epoch | 远端 token 的 epoch 首次检查失败后，到轮询确认有效 |
| 本地转发空间 | 本地 NVL 转发缓冲已满，到 Forwarder 获得可用位置 |

local 和 remote 同时有未处理 token 时，找来源的时间独立记为 `mixed_tail`。其余 Forwarder 时间单列。按同一通道、同一 warp 做周期差，报告原始周期及按 nominal clock 换算的估计微秒。各通道并行，等待只在各自 Forwarder 范围内对账。`receiver_comparison.csv` 里的差值用来确定优先排查方向，完整 API 的可追回时间需要干预复证。

## 两端执行

两端同步本文档所在提交并从仓库根目录运行。保持 `6.6.0-gdr_f00955680_diag`。202 设 `RANK=0`，204 设 `RANK=1`。先在两端各自构建和预检，可以在不同时间进行。

```bash
bash test/reference_events/run_same_kernel_gap.sh --study waits --action setup --node-rank "$RANK"
```

看到 `SAME_KERNEL_WAITS_SETUP_PASSED` 后，两端在同一个双机窗口分别执行一次。

```bash
bash test/reference_events/run_same_kernel_gap.sh --study waits --action run --node-rank "$RANK"
```

按 RDMA 无采集 → 采集 → 无采集、URMA 同样三轮运行。固定 Normal BF16、4096 tokens/节点、chunk 32、URMA 仅 204 tail-off，每轮 5 预热和 10 采样，汇总用后 9 条。保持原有正确性检查。任务名 `normal_same_kernel_waits_01`，master `141.61.17.202`，端口 `34340–34351`。两端看到 `SAME_KERNEL_WAITS_RUN_PASSED` 即可。构建默认在 `/home/h00918771/metax_dev/normal_waits_build/`，源码树为 `/home/h00918771/metax_dev/normal_waits_src_v1`。`--stream-tree`、`--verbs-build`、`--urma-build` 可以覆盖默认路径。已有基线模块及旧诊断模块继续可用。

## 上传和验收

上传两端 `test/reference_events/results/normal_same_kernel_waits_01/` 的六轮原始日志和预检记录。重点保留两个 202 probe 下的 `stream_timing.jsonl`、所有 `api_samples.jsonl`、`status.json`、`overlay_node*.json`、模块与主机身份，以及 GPU 快照和 `console`。日志齐全后复算如下。

```bash
python3 tools/reference_events/summarize_stream_timing.py --receiver-waits \
  --root test/reference_events/results/normal_same_kernel_waits_01 \
  --output test/reference_events/results/normal_same_kernel_waits_01/summary
```

1. 先看 `decision.json`。对照轮必须仍为 202 最晚，CV、漂移与扰动在 5% 内。还要确认新构建的无采集后端差额与此前约 661 μs 的差距，以及启用采集前后的差额，变动都不超过 100 μs。这样能同时检查编译加入计数代码和实际启用计数的影响。
2. 先用 `comparison.csv` 检查主 Dispatch 是否承担主要额外时间。再看 `receiver_comparison.csv` 和 `latest_receiver.csv`，核对最晚通道哪一类等待在多个样本持续偏长。查到远端 tail 则聚焦发送与通知到达，查到 epoch 则聚焦 tail 后的数据写入可见性，查到本地空间则聚焦 202 的输出消费。
3. 若主要差额落在 `mixed_tail` 或其他残余，保留该范围和未解释时间。若验收失败，保留日志并停止原样复跑。
