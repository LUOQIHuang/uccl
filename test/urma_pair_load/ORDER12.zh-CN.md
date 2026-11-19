# URMA 窗口的两项实验

先运行[完整 Normal 密集采样](../fadd_path/DENSE.zh-CN.md)中的 `fadd_dense_01`。它保留原 Normal 的调度和顺序设置，用 `pre → probe → post` 判断插桩影响，并拆出 FADD 返回值首次可读之后，SDK 轮询调用内和两次调用之间的时间。优先保留两个节点的完整原始记录，分析可以离开设备后执行。

窗口有余量时再跑下面的提交顺序对照。两项实验顺序执行，各自独立构建。已有[深度对照结果](results/urma_pair_depth12_01/ANALYSIS.zh-CN.md)继续用于历史比较。

## 提交顺序对照验证什么

三队列深度 4 已把首个 FADD 的完成观测推迟约 228–241 μs，同时整批耗时略降。本次固定三队列、每队列四对，仅改变同一条队列的请求排列。

| 配置 | 每条队列的单次 SDK 提交 |
| --- | --- |
| `writes_then_fadds` | `W0 W1 W2 W3 A0 A1 A2 A3` |
| `interleaved` | `W0 A0 W1 A1 W2 A2 W3 A3` |

两组每迭代都是 12 条 459264 字节 WRITE 和 12 条加 32 的 FADD。每队列一次链式提交，三次提交完成后开始轮询。GPU payload 和 GPU tail 计数器、每请求完成、WRITE RELAX、FADD STRONG 与 per-request fence、CPU192 和单向负载均固定。A 是与 B 同一模块和同一窗口下的必要控制。两组在同一个 `run` 中各做两个方向、三轮，偶数轮倒序。每测点完成 payload 和最终计数器校验。

实际 SDK 链中的请求编号会写入 `samples.jsonl`，分析器按队列拒绝不符合目标顺序的样本。两个主要指标是首次 WRITE 提交到首个 FADD 完成观测的时间，以及同样 12 对的整批完成时间。另记录最后 WRITE、最后 FADD 和逐对 W→A 分布。完成时刻均由发送端软件观测。是否推迟接收 GPU 消费仍需完整 Normal 的接收端时序。

## 两端操作

从最新分支更新，在仓库根目录运行。沿用上一轮有效的 URMA 内核、MACA 3.9 和私有 SDK 路径。202 设置 `RANK=0`，204 设置 `RANK=1`，两端都用 CPU192。

```bash
export URMA_DIAG_HOME=/home/h00918771
export VERBS_DIAG_HOME="$URMA_DIAG_HOME"
export URMA_PAIR_LOAD_PYTHON="$URMA_DIAG_HOME/metax_dev/venv-maca-py312/bin/python"
export URMA_DIAG_PYTHON="$URMA_PAIR_LOAD_PYTHON"
export VERBS_DIAG_PYTHON="$URMA_PAIR_LOAD_PYTHON"
export MACA_PATH=/opt/maca-3.9.0
export URMA_DIAG_LIBRARY_DIR="$URMA_DIAG_HOME/metax_dev/urma_after_kernel_20261029/diag/umdk_private_618957c3/lib"
export VERBS_DIAG_LIBRARY_DIR="$URMA_DIAG_LIBRARY_DIR"
export URMA_DIAG_INCLUDE_DIR="$URMA_DIAG_HOME/metax_dev/urma_sdk_private_include"
RANK=0 # 204改为1
JOB=urma_pair_order12_01
TREE="$URMA_DIAG_HOME/metax_dev/urma_pair_load_src_v4"
BUILD="$URMA_DIAG_HOME/metax_dev/urma_pair_load_build/urma_v4"
ROOT="$URMA_DIAG_HOME/metax_dev/urma_pair_load_results"
DEST="$PWD/test/urma_pair_load/results"

urma_order() {
  bash test/urma_pair_load/run.sh --action "$1" \
    --backend urma --suite order12 --node-rank "$RANK" --job "$JOB" \
    --master-addr 141.61.17.202 --port 34480 \
    --worker-cpu 192 --rounds 3 --warmups 20 --samples 101 \
    --tree "$TREE" --build-dir "$BUILD" --output "$ROOT" --destination "$DEST"
}
urma_order prepare && urma_order build && urma_order check
```

构建和检查可在两端各自完成。202 先执行 `run`，204 在 180 秒内启动同一 JOB。成功标志为 `URMA_PAIR_LOAD_RUN_PASSED`。如中断，可用 `urma_order collect` 补收已经生成的文件。

```bash
urma_order run
```

提交结果目录 `test/urma_pair_load/results/urma_pair_order12_01/urma/`。保留两个节点的 `samples.jsonl`、`paths.jsonl`、`plan.json`、`affinity.json`、`status.json`、`driver_status.json`、环境与构建记录。若双端文件齐全，可在设备窗口结束后生成汇总。

```bash
"$URMA_PAIR_LOAD_PYTHON" test/urma_pair_load/analyze.py \
  --root "$DEST/$JOB" --output "$DEST/$JOB/summary_urma"
```

如果交替排列让首个 FADD 的完成明显提前，而且整批耗时持平或下降，链内顺序就值得在原 Normal 中验证。如果首个 FADD 提前但整批变慢，按接收侧关键路径评估这项取舍。如果两项指标变化小，继续看完整 Normal 的返回值后延迟及轮询服务情况。原 Normal 的提交策略需要另行验证 GPU 接收、正确性和端到端收益。
