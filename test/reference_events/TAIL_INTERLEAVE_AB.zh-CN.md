# 完整 Normal 的 Dispatch tail FADD 交替提交 A/B

三队列微基准中，WRITE 后紧跟对应 FADD，使首个完成提前约 210 μs，整批耗时缩短约 1.5%。这次验证完整 Normal 中的两个结果：接收 GPU 能否更早取得可消费的 token，以及双端 BF16 Dispatch 耗时是否下降。[微基准结果](../urma_pair_load/results/urma_pair_order12_01/ANALYSIS.zh-CN.md)

两端在同一编译模块中切换。A 使用 `lane-mixed`。B 使用 `tail-interleaved`，对同一批中相邻且能校验同 ring、同目的端、token 数一致的 Dispatch payload WRITE 和 tail FADD，将该 FADD 放到对应 WRITE 后面。遇到未配对的原子命令后，保留该 ring 后续原子的原顺序。包含显式 WRITE/FADD 通知的 ring 沿用既有处理。保持原请求 ID、WRITE RELAX、FADD STRONG 和 fence、全量完成、原软件窗口和 quiet/barrier。跨批次拆开的配对沿用原路径，采集阶段会验证实际提前了多少对。

## 准备并运行

使用已验证的 URMA 内核 `6.6.0-gdr_f00955680_diag`、私有 SDK 和 MACA 3.9。两端拉取同一提交，在仓库根目录分别执行以下设置，202 用 `RANK=0`，204 用 `RANK=1`。私有 SDK 环境变量如有更新，沿用已通过 `fadd_dense_01` 的值。

```bash
export URMA_DIAG_HOME=/home/h00918771
export URMA_DIAG_PYTHON="$URMA_DIAG_HOME/metax_dev/venv-maca-py312/bin/python"
export MACA_PATH=/opt/maca-3.9.0
export URMA_DIAG_LIBRARY_DIR="$URMA_DIAG_HOME/metax_dev/urma_after_kernel_20261029/diag/umdk_private_618957c3/lib"
export VERBS_DIAG_LIBRARY_DIR="$URMA_DIAG_LIBRARY_DIR"
export URMA_DIAG_INCLUDE_DIR="$URMA_DIAG_HOME/metax_dev/urma_sdk_private_include"
RANK=0 # 204 改为 1
JOB=urma_normal_tail_order_01
BUILD="$URMA_DIAG_HOME/metax_dev/reference_event_build/urma_tail_order_v1"
ROOT="$URMA_DIAG_HOME/metax_dev/reference_event_results"
DEST="$PWD/test/reference_events/results"

bash test/reference_events/run.sh --backend urma --action build --step trace \
  --reference-profile order --node-rank "$RANK" --job "${JOB}_build" \
  --build-dir "$BUILD" --output "$ROOT"
```

build 可在两端独立完成。先留时间跑 `performance`。202 启动后，在现有连接超时范围内从 204 执行同一阶段。脚本先跑 128-token B 正确性门禁，随后自动跑固定 4096 tokens 的 A/B/B/A。每个正式点预热 5 次、记录 10 次，丢首样本后各 9 次。性能阶段关闭 GPU/CPU 事件采集。

```bash
normal_tail_order() {
  bash test/reference_events/run_submit_ab.sh --candidate tail-interleaved --part "$1" \
    --node-rank "$RANK" --master-addr 141.61.17.202 --port 34620 \
    --build-dir "$BUILD" --job "$JOB" --output "$ROOT" --destination "$DEST"
}
normal_tail_order performance
```

若性能阶段已结束且窗口有余量，两端再各执行一次下面命令。它连续跑 A、B 各一个带采集的正式点。每个节点的本地首个发送 token 用作时间基准，分别记录首个和最后一个入站 token 通过 epoch 校验的时间。CPU 记录同时验证 B 的 tail FADD 是否在同一 SDK 链里跨过了后续 WRITE。

```bash
normal_tail_order trace
```

每项通过时应看到 `SUBMIT_AB_PASSED`。出现失败时，脚本收集当前项日志后停止。保留 `test/reference_events/results/` 下所有以 `urma_normal_tail_order_01_` 开头的双端目录，包括 smoke、四个 paced 结果和已完成的两个 trace 结果。GPU 窗口内优先采集。离线汇总可稍后做。

```bash
"$URMA_DIAG_PYTHON" tools/reference_events/summarize_submit.py \
  --candidate tail-interleaved --root "$DEST" --job "$JOB" --output "$DEST/${JOB}_performance_summary"
"$URMA_DIAG_PYTHON" tools/reference_events/summarize_order_trace.py \
  --root "$DEST" --job "$JOB" --output "$DEST/${JOB}_trace_summary"
```

性能主指标为对每个 BF16 调用取双端本地耗时的较大值，再计算每轮均值和变化。FP8、Combine 和 layout 作为其他结果同时保留。采集结果用同策略 paced 对照评估，BF16 的扰动和 CV 各需在 5% 内才能用于解释性能。先确认 `ORDER_TRACE_CHAIN_CHANGE_OBSERVED`。若可消费时间提前且性能双轮同向改善，才有完整 Normal 收益的证据。若实际提前的通知很少，优先定位配对覆盖，避免把空干预当作已否定该机制。
