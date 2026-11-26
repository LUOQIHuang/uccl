# 同内核补测 RDMA Normal Dispatch 的 FIFO 阻塞

先在当前内核完成 [4096-token 端到端差距](../reference_events/SAME_KERNEL_GAP.zh-CN.md)。如果要定位差距，接着跑本实验。旧 RDMA 阶段记录已有命令收取、提交、CPU 取到 CQE、名额回收的时间，缺少“下一条命令已在 FIFO 且 8 个名额已占满”的起点。最新 URMA 中，204 最晚被 Proxy 收走的第 6 块在此区间等待下界中位数为 **677.32 μs**，9/9 次实际卡在前序第 2 条 WRITE。本次补上同一定义的 RDMA 等待，一次双机运行就能回答 RDMA 是否也有同样长的发送背压、卡在什么队首请求。 [历史 RDMA 阶段记录](../reference_events/VERBS_MATCHED.zh-CN.md) · [URMA 实测](../normal_completion/results/normal_blockers_04/ANALYSIS.zh-CN.md)

固定 Normal BF16 Dispatch、4096 tokens/节点、hidden 7168、256 experts、top-k 8、chunk 32、软件窗口 8。每轮预热 5 次、采样 10 次，汇总保留后 9 次。`pre → probe → post` 使用同一探针构建，只有中间 `probe` 开启 CPU 事件，只有 BF16 Dispatch 采集。Proxy 在窗口满时每 32 次访问对下一条 FIFO 命令做一次只读检查。采集期间仍执行原有正确性检查。

| 观察什么 | 可回答的问题 |
| --- | --- |
| 首次确认命令在 FIFO 且窗口已满 → Proxy 收走 | 后续 WRITE 最少等了多久，在哪条通道最晚推进 |
| 窗口满时的实际队首 → CPU 从 `ibv_poll_cq` 取得该 WR 的完成 | 队首是 WRITE、原子还是其他命令，等待落在提交前、提交中还是提交后 |
| CPU 取到 CQE → 软件释放在途名额 | 完成已经被读到之后，有序回收是否还拖住了窗口 |

RDMA 的完成边界是 CPU 观察时刻。URMA 还读了私有 provider 的 CQ owner 位，因此已有“CQE 尚不可读”的下界。两边这次先比较 FIFO 等待和实际挡住它的 WR。设备 CQE 何时变得可读、实际网络传输时间、以及能从整体 Dispatch 追回多少，需要各自对应的证据。未观测到窗口满时的 FIFO 命令以空值和覆盖数报告。不同通道并行，等待时长分别统计。

## 两端运行

1. 两端拉取包含本文档的提交并进入仓库根目录。保持 `6.6.0-gdr_f00955680_diag`。202 执行 `RANK=0`，204 执行 `RANK=1`。默认数据网卡分别是 `rocep161s0f0`、`rocep40s0f0`，port 1 / RoCE v2 / GID 3。控制接口默认分别为 `enp40s0`、`enp34s0f5u1u4`。
2. 双机机时之前，两端独立执行构建和预检。这个步骤无须对端在线。

   ```bash
   bash test/verbs_fifo/run.sh --action setup --node-rank "$RANK"
   ```

   看到 `VERBS_FIFO_SETUP_PASSED` 后可以启动正式分段采集。脚本默认使用 `/home/h00918771/metax_dev/verbs_same_kernel_fifo_src_v1` 作为隔离工作树，使用 `/home/h00918771/metax_dev/verbs_same_kernel_build/fifo_v1` 作为构建目录。可通过 `--tree`、`--build-dir` 指定新的路径，`setup` 和 `run` 使用相同路径。原端到端对照继续用它已有的纯 verbs 模块。
3. 两端各执行一次，按首尾控制夹住一次探针运行。默认 master `141.61.17.202`、端口范围 `34220–34225`。若环境实际设备有变化，各端可通过 `--hca`、`--ifname` 指定，`--gid-index`、`--master-addr`、`--port`、`--job` 需双方一致。

   ```bash
   bash test/verbs_fifo/run.sh --action run --node-rank "$RANK"
   ```

   两端看到 `VERBS_FIFO_RUN_PASSED` 即可。正式运行目录已有数据时，重跑使用新的 `--job`。

## 回传和复算

回传双方 `test/verbs_fifo/results/verbs_same_kernel_fifo_01/` 的内容。尤其保留 `overlay_node*.json`、三轮的 `setup.json`、`host.json`、`module_manifest.json`、`status.json`、`runtime.json`、`bench.log`、`api_samples.jsonl`、`reference_control.jsonl` 和 probe 的 `fifo_trace.jsonl`。GPU 快照及 benchmark 副本也用于确认条件一致，其余日志随目录归档。失败时回传已有的 `console` 和三轮日志。

```bash
python3 tools/reference_events/summarize_verbs_fifo.py \
  --root test/verbs_fifo/results/verbs_same_kernel_fifo_01 \
  --output test/verbs_fifo/results/verbs_same_kernel_fifo_01/summary
```

先看 `controls.csv`。两端的控制漂移、探针偏移、组内 CV 都在 5% 以内，才用 `summary.csv` 的结果做归因。重点看 204 最晚第 6 块的覆盖数和 `wait_median_us`，再看 `latest_channels.csv` 的队首、`head_intervals.csv` 的各段。已覆盖样本的 RDMA 等待显著短于 URMA 时，就能把剩余差距的排查优先级放在发送窗口释放之前。若两边同样长，则应回查哪个通道与接收 GPU 的最后有效 token 有关联。覆盖不足时保留未知。
