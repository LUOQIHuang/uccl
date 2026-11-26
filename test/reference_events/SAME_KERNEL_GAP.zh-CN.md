# 同内核测量 URMA 缩短了多少 RDMA 差距

历史 202 端的 Normal BF16 Dispatch 对照中，verbs 平均 7.149 ms，URMA 全开 fence 平均 8.114 ms，差 0.965 ms（13.50%）。另一组 URMA 反向配对中，关闭 204 的数据 FADD fence 后从 8.084 降到 7.806 ms，节省 277.52 μs。把该组优化前后与同一个历史 verbs 数字比较，差距估算从 0.935 降到 0.657 ms，追回约 29.7%。这一估算跨测试批次和内核，关闭范围覆盖 204 的 tail 通知和数据反馈。 [历史后端对照](results/backend_matched_comparison_01/api_comparison.csv) · [URMA 反向配对](results/urma_fadd_fence_01_summary/ANALYSIS.zh-CN.md)

本轮在同一次启动的 `6.6.0-gdr_f00955680_diag` 上直接测三种状态：纯 verbs + DMA-BUF、URMA 全开 fence、URMA 仅 204 tail FADD 关闭 fence。两端已经在此内核验证纯 verbs 的 GPU DMA-BUF、WRITE 和原生 FADD。 [202 验收](results/verbs_same_kernel_01/console/node0/smoke.ZICLOn/path_proof.log) · [204 验收](results/verbs_same_kernel_01/console/node1/smoke.0lHpoZ/path_proof.log)

| 顺序 | 配置 | 用途 |
| --- | --- | --- |
| 1 | verbs | 复测 RDMA 基线 |
| 2、3 | URMA 全开 → 仅 204 tail-off | 测第一组节省 |
| 4、5 | URMA 仅 204 tail-off → 全开 | 反向测第二组节省 |
| 6 | verbs | 检查首尾基线漂移 |

固定 Normal BF16 Dispatch、4096 tokens/rank、hidden 7168、top-k 8、256 experts、chunk 32 和软件在途窗口 8。沿用 `paced` 计时，每组预热 5 次、记录 10 次，汇总保留索引 1–9。每次取两端本地 API 时长中的较大值，再求均值。关闭阶段采集和 profiler。现有 benchmark 仍执行功能校验并保留其它采样行，汇总只读取 BF16 chunk 32 的 Dispatch。

## 两端运行

1. 两端更新到包含本文档的提交，进入仓库根目录。202 执行 `RANK=0`，204 执行 `RANK=1`。保持 `uname -r` 为 `6.6.0-gdr_f00955680_diag`，从正式测试开始到结束不要重启。默认数据设备如下。

   | 节点 | 纯 verbs，port 1 / GID 3 | URMA | 控制接口 |
   | --- | --- | --- | --- |
   | 202（rank 0） | `rocep161s0f0` | `udmac1d1e2` | `enp40s0` |
   | 204（rank 1） | `rocep40s0f0` | `udmac0d1e2` | `enp34s0f5u1u4` |

2. 两端各自执行预检。默认复用上次的构建，无需重新编译。预检可分别运行。

   ```bash
   bash test/reference_events/run_same_kernel_gap.sh --action check --node-rank "$RANK"
   ```

   预期看到 `SAME_KERNEL_GAP_CHECK_PASSED`。默认 verbs 扩展在 `/home/h00918771/metax_dev/verbs_same_kernel_build/current_v1`，URMA 扩展在 `/home/h00918771/metax_dev/writes_first_batch_build/urma_v1`。若已有模块放在其它位置，从预检到正式运行一直传相同的 `--verbs-build` 和 `--urma-build`。若缺构建或预检报告源码不匹配，用未占用的新目录执行对应命令，然后将该目录传给上面的预检和下面的正式运行。构建可在双机机时前完成。

   ```bash
   # 只在需要重建时，分别使用空目录
   VERBS_BUILD=/home/h00918771/metax_dev/verbs_same_kernel_build/gap_v1
   URMA_BUILD=/home/h00918771/metax_dev/writes_first_batch_build/gap_v1
   bash test/verbs_baseline/run_on_diag.sh --action build --node-rank "$RANK" --build-dir "$VERBS_BUILD" --job normal_gap_verbs_build
   bash test/reference_events/run_writes_first_batch.sh --action build --node-rank "$RANK" --build-dir "$URMA_BUILD" --job normal_gap_urma_build
   ```

3. 空闲双机窗口内，202 和 204 各启动一次。脚本依次执行表中六轮，不需要切内核或逐轮输入命令。

   ```bash
   bash test/reference_events/run_same_kernel_gap.sh --action run --node-rank "$RANK"
   ```

   两端都应出现 `SAME_KERNEL_GAP_RUN_PASSED`。默认 rendezvous 地址 `141.61.17.202`，使用 34180–34191。若要调整地址、端口或任务名，两端一致传 `--master-addr`、`--port` 或 `--job`。若某轮失败，保留已归档的输出。需要重跑时两端换一个相同的 `--job`。

## 汇总和回传

回传 `test/reference_events/results/normal_same_kernel_gap_01/` 下双方产生的原始结果，尤其是六轮的 `setup.json`、`host.json`、构建记录、`status.json`、`runtime.json`、`api_samples.jsonl`、`sample_summary.jsonl`、`bench.log` 和 `console`。目录内的 benchmark 副本、采样控制文件和 GPU 快照也用于同口径校验，请一并保留。若预检或中途失败，回传同一目录下已有的内容。

双方文件齐全后可复算（汇总输出目录需尚未存在）。

```bash
python3 tools/reference_events/summarize_same_kernel_gap.py \
  --root test/reference_events/results/normal_same_kernel_gap_01 \
  --output test/reference_events/results/normal_same_kernel_gap_01/summary
```

以首尾两轮 verbs 的均值为 `R`，两轮全开为 `A`，两轮 tail-off 为 `B`。优化前差 `A−R`、优化后差 `B−R`、追回 `A−B`。剩余慢百分比是 `(B−R)/R`，追回占原差距的比例是 `(A−B)/(A−R)`。`gap.json` 同时给出两组配对各自的节省、轮内 CV 和同配置两轮漂移。两组配对同向、CV 不超过 5%、各配置两轮漂移不超过 3% 时，使用该窗口的均值汇报；超出时同时列出每轮结果和漂移。

定位剩余差距时，继续使用 [同内核 RDMA FIFO 补测](../verbs_fifo/README.zh-CN.md)。探针可以提前构建，正式采样放在本轮端到端计时完成之后。
