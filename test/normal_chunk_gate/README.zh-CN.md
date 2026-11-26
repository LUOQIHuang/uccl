# 验证 204 第 6 块是否拖慢 202 的 Dispatch

**本轮已完成。** 两对配对中，204 第 6 个 WRITE 的实际额外等待中位数为 302.05 和 302.25 μs，202 Dispatch 分别变化 +5.49 和 −18.09 μs。同配置重复漂移最高 0.26%。本次干预没有观察到延迟传到整体结束时间，因此降低该位置的排查优先级。此前观测到的 204 局部 CQE 等待仍成立。下列步骤用于复现，当前无需重复执行。 [四轮数据](results/normal_chunk6_gate_01/summary/rounds.csv) · [配对结果](results/normal_chunk6_gate_01/summary/decision.json)

目前定位到了 204 局部阻塞的边界。第 6 块已经在 FIFO，前序第 2 条 WRITE 占着最后一个可用名额，窗口满且 CQE 未可读的时间下界中位数是 **671.90 μs**。同内核无打点对照中，优化后 URMA 仍慢 **619–661 μs**，202 每次都更晚结束。上一轮 GPU 接收打点把两后端的差额移动了 872 μs，不能据此量化关键路径。 [204 逐请求证据](../normal_completion/results/normal_blockers_04/ANALYSIS.zh-CN.md) · [无打点和打点结果](../reference_events/results/normal_same_kernel_receive_01/summary/decision.json)

本轮同时检查 **204 的第 6 块确实晚交付了多久** 和 **202 的完整 Dispatch 是否跟着变慢**。在 204，Proxy 发现第 6 个大 WRITE 已经可收取时，在对应通道上再等 300 μs。这个等待只延后该通道当前命令和排在它后面的命令。Proxy 继续轮询完成队列、回收名额、服务其它通道。12 个数据通道各触发一次。202 只记录原有端到端时延。两端均不启动 GPU 时间线或 CPU 逐请求采集。

| 顺序 | 204 第 6 个大 WRITE 的额外等待 | 用途 |
| --- | ---: | --- |
| A1 | 0 μs | 同构建控制 |
| B1 | 300 μs | 第一组对照 |
| B2 | 300 μs | 反向配对 |
| A2 | 0 μs | 检查控制复现 |

四轮均使用当前生产路径的传输和完成设置，保持 202 fence 全开、204 仅 tail-off、窗口 8、ordered、lane-mixed。相同诊断模块的 A、B 轮都记录这 12 个触发点，只有 B 轮延后收取。保持原来的正确性检查。测量 Normal BF16 Dispatch，固定每节点 4096 tokens、chunk 32、5 次预热和 10 次采样。只保留索引 1–9，以 202 本地时延和每次两节点时延的最大值为主要结果。

## 运行

1. 两端同步本分支，保持 `6.6.0-gdr_f00955680_diag` 并进入仓库根目录。202 设 `RANK=0`，204 设 `RANK=1`。设备沿用 `udmac1d1e2`、`udmac0d1e2`，接口沿用 `enp40s0`、`enp34s0f5u1u4`。两端可以分别提前构建，未占用双机 benchmark 窗口。

   ```bash
   bash test/normal_chunk_gate/run.sh --action setup --node-rank "$RANK"
   ```

   等待 `NORMAL_CHUNK_GATE_SETUP_PASSED`。隔离树和模块默认位于 `/home/h00918771/metax_dev/normal_chunk6_gate_src_v1`、`/home/h00918771/metax_dev/normal_chunk6_gate_build/urma_v1`。需要改目录时同时更新 `setup` 和 `run` 的 `--tree`、`--build-dir`。原有 URMA 和 RDMA 构建可继续使用。

2. 两端各运行一次。默认 master `141.61.17.202`、端口 `34300–34307`，任务名 `normal_chunk6_gate_01`。自定义 `--job`、`--master-addr` 或 `--port` 时两端使用一致的值。

   ```bash
   bash test/normal_chunk_gate/run.sh --action run --node-rank "$RANK"
   ```

   等到两端出现 `NORMAL_CHUNK_GATE_RUN_PASSED`。脚本自动归档。失败时保留已产生的目录，重跑使用新的 `--job`。

## 回传和判断

回传双方 `test/normal_chunk_gate/results/normal_chunk6_gate_01/`。保留两端四轮的 `setup.json`、`host.json`、`build_manifest.json`、`status.json`、`runtime.json`、`api_samples.jsonl`、`sample_summary.jsonl`、`bench.log`、GPU 快照、benchmark 副本和控制台。204 的每轮 `normal/chunk_gate.jsonl` 用于验证实际等待，`overlay_node*.json` 用于确认构建来源。只需归档日志和元数据。

```bash
python3 tools/reference_events/summarize_chunk_gate.py \
  --root test/normal_chunk_gate/results/normal_chunk6_gate_01 \
  --output test/normal_chunk_gate/results/normal_chunk6_gate_01/summary
```

汇总核对每个窗口 12 个通道是否都按设置触发，以及 A、B 各自重复是否稳定。以 `decision.json` 中两个配对的 `node202_us` 和 `paired_max_us` 为准。确认实际推迟约 300 μs 后，若 202 在两个配对里均随之出现同量级变慢，说明该段发送进度对 202 结束时间有约束，后续才值得区分队首 WRITE 的设备执行、网络确认和 CQE 发布。若 202 变化接近重复噪声，而 204 的第 6 块确实被推迟，则优先回查其它阶段和通道。结果是新增延迟对当前路径的影响，无法直接换算为可消除的 619–661 μs 或给出设备内部根因。
