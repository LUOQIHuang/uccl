# 同内核减少反方向载荷的 Normal Dispatch 对照

**新版默认任务为 `normal_reverse_load_02`。** `_01` 两次在 `rdma_half` 的随机 top-k 权重校验处停止。202 的远端 token 数已从 4076 降到 2046，204 保持 4080。Dispatch 对屏蔽槽位写零，旧参考仍含原随机权重。新版按实际 `topk_idx == -1` 更新随机权重参考，保留原断言及其它正确性检查。`_01` 没有完整半载荷计时。 [失败复现记录](results/normal_reverse_load_01/ERROR_REPORT_v2.zh-CN.md)

上轮等待计数构建在采集关闭时就将 RDMA 从 7.538 ms 拉到 13.595 ms、URMA 从 8.127 ms 拉到 13.751 ms。该构建的分段不再用于解释原来的 **588.67 μs** 差距。此次复用原有二进制，直接改变一个负载条件，检验剩余差距是否随反向数据量变化。 [等待计数验收](results/normal_same_kernel_waits_02/summary/decision.json) · [原基线](results/normal_same_kernel_stream_01/summary/decision.json)

**保持 204→202 的 token 路由完全不变，把 202→204 的远端数据载荷降到约一半。** 各端仍输入 4096 tokens，保留本地专家路由。202 只对原本同时发往本地和远端的 token 隔一个取消其远端路由，因而这些 token 仍有本地目标。原 Normal Dispatch、收发流程、正确性检查及计时保持不变。单测量点记录实际本地和远端 token 数、路由指纹，验证同一配置下两后端使用相同路由输入。RDMA 和 URMA 各按全量 → 反向减半 → 全量运行一次。 [历史双向推进证据](NORMAL_REVIEW_20261123.zh-CN.md)

比较的是 **URMA 与 RDMA 的差距在反向减半后变化多少**，以及 202 本地时延如何响应。202→204 数据减少也会减轻 204 接收和相关反馈的工作。如果差距在稳定控制下显著缩小，说明双向负载及相关依赖对差距有贡献，还需区分竞争资源或反馈环节。如果差距大致保留，应优先看 204→202 路径及与反向数据量无关的固定成本。该实验识别负载依赖，具体设备阶段仍需对应证据。

## 两端执行

两端更新到本页所在提交并进入仓库根目录。保持 `6.6.0-gdr_f00955680_diag`。202 设 `RANK=0`，204 设 `RANK=1`。采用与 [正式同内核对照](SAME_KERNEL_GAP.zh-CN.md) 相同的原 verbs 和 URMA 模块。无需重新编译，预检可分别执行。

```bash
bash test/reference_events/run_same_kernel_gap.sh --study reverse --action check --node-rank "$RANK"
```

两端均看到 `SAME_KERNEL_REVERSE_CHECK_PASSED` 后，在同一个双机窗口分别启动。

```bash
bash test/reference_events/run_same_kernel_gap.sh --study reverse --action run --node-rank "$RANK"
```

固定 Normal BF16 Dispatch，hidden 7168、top-k 8、256 experts、chunk 32，URMA 仅 204 tail-off。每项预热 5 次、采样 10 次，汇总使用后 9 条。沿用原 benchmark 流程，报告只读取 BF16 Dispatch。默认任务 `normal_reverse_load_02`、master `141.61.17.202`、端口 `34360–34371`。两端看到 `SAME_KERNEL_REVERSE_RUN_PASSED` 即可。需要指定已有构建时，两条命令都传相同的 `--verbs-build`、`--urma-build`。

## 回传与判定

回传 `test/reference_events/results/normal_reverse_load_02/` 两端的六轮及预检数据。必须保留六轮的 `bench.log`（含实际路由记录）、`api_samples.jsonl`、`sample_summary.jsonl`、`status.json`、构建和主机身份、benchmark 副本及 `console`。失败时保留已有记录。

```bash
python3 tools/reference_events/summarize_reverse_load.py \
  --root test/reference_events/results/normal_reverse_load_02 \
  --output test/reference_events/results/normal_reverse_load_02/summary
```

`routing.csv` 验证实际负载，`controls.csv` 检查前后控制漂移和组内 CV，`comparison.csv` 给出 202、204、每样本两端取较长值三种口径下的全量差距、反向减半差距及差额变化。差额变化要与两个全量控制轮的波动一起判断。这一轮只做一个减半条件。
