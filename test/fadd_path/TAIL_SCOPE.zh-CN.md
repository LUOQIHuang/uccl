# URMA Normal 下一轮：确认 tail-off 后剩余的完成等待

本轮只计时 **Normal BF16 Dispatch，4096 tokens/rank、hidden=7168、experts=256、top-k=8、SM=24、NVL/RDMA chunk=4/32**。固定 URMA 环境，只比较两端全开 fence（A）与 204 只关 tail fence（C）。两端仍执行原 Normal 正确性检查，包含 FP8 和 Combine 校验。性能计时只有 BF16 Dispatch。

此前开 fence 时，204 的 FADD 返回值已经可读，到 SDK 交付对应 CQE 仍有约 285 μs 的中位等待下界。本轮要回答的是该等待在 C 下还剩多少、202 最后一批 token 是否随之更早可消费。若 C 仍有长等待且 Proxy 在短间隙内持续轮询，优先查匹配实机版本的 provider 和设备 CQE 发布。若完成交付已经提前而 202 仍慢，优先查后续 WRITE 与接收 GPU 的数据可见性。详见[先前采样](results/fadd_dense_01/ANALYSIS.zh-CN.md)和[三配置结果](../reference_events/results/urma_fence_scope_01_ANALYSIS.zh-CN.md)。

| 记录 | 位置 |
| --- | --- |
| 完整 Dispatch | 两端每次调用分别计时，同序号取较大者。只有关闭探针的前后两组用于净性能对比 |
| 提交 FADD → 本地返回值可读 → SDK 交付对应 CQE | 204 的 4 个 Proxy，每个采 ring 0 第 6 条 WRITE 的对应 tail FADD。只读现有返回槽，实际 fence 位随样本核验 |
| 首次本地 WRITE／首个本地 token → 最后远端 token 通过 epoch 检查 | 202 的 GPU，在同机时钟域统计全部 12 个通道，复用已有三个稀疏事件 |

正式执行 6 个 BF16 case，顺序为 `A_pre → C_pre → A_probe → C_probe → C_post → A_post`。只在两项 probe 中开启观测，202 开稀疏 GPU 事件和对应映射时钟，204 开 FADD poll 探针。每项预热 5 次、采样 10 次、弃首值。保持 WRITE RELAX、FADD STRONG、默认窗口 8、全量完成与 lane-mixed。两组 probe 各用自己的前后关闭探针对照。生产默认和 GPU 内核源码保持原行为。

## 1. 两端环境

使用可跑 URMA 的 `6.6.0-gdr_f00955680_diag`。202 设置 `RANK=0`，204 设置 `RANK=1`。保留上一轮已经验证的私有库、AtomicOps 和 GPU/设备环境。用新的 TREE 和 BUILD，旧 FADD 模块缺少 tail 角色代码。

```bash
cd /home/h00918771/uccl-ep-xpu-integration-new
set -euo pipefail
export URMA_DIAG_HOME=/home/h00918771
export VERBS_DIAG_HOME="$URMA_DIAG_HOME"
export URMA_DIAG_PYTHON="$URMA_DIAG_HOME/metax_dev/venv-maca-py312/bin/python"
export VERBS_DIAG_PYTHON="$URMA_DIAG_PYTHON"
export FADD_PATH_PYTHON="$URMA_DIAG_PYTHON"
export MACA_PATH=/opt/maca-3.9.0
export URMA_DIAG_LIBRARY_DIR="$URMA_DIAG_HOME/metax_dev/urma_after_kernel_20261029/diag/umdk_private_618957c3/lib"
export VERBS_DIAG_LIBRARY_DIR="$URMA_DIAG_LIBRARY_DIR"
export URMA_DIAG_INCLUDE_DIR="$URMA_DIAG_HOME/metax_dev/urma_sdk_private_include"

RANK=0                       # 204 改为 1
JOB=urma_fadd_tail_scope_01   # 两端保持一致
TREE="$URMA_DIAG_HOME/metax_dev/fadd_tail_scope_src_v1"
BUILD="$URMA_DIAG_HOME/metax_dev/fadd_path_build/urma_tail_scope_v1"
ROOT="$URMA_DIAG_HOME/metax_dev/fadd_path_results"
DEST="$PWD/test/fadd_path/results"

uname -r
mx-smi
test -f "$URMA_DIAG_INCLUDE_DIR/urma_api.h"
test -d "$URMA_DIAG_LIBRARY_DIR"
tail_scope() {
  bash test/fadd_path/run.sh --action "$1" --tail-scope \
    --backend urma --poll-stride 1 --node-rank "$RANK" --job "$JOB" \
    --master-addr 141.61.17.202 --port 33980 \
    --tree "$TREE" --build-dir "$BUILD" --output "$ROOT" --destination "$DEST"
}
```

若本次协调的端口不同，两端统一修改 `--port`，并确认它和 `port+1` 均空闲。`prepare` 只需要 Git 和 Python，可在获得设备前执行。若使用浅克隆并缺少 `d822cf31`，先取得该分支包含该提交的历史。

## 2. 准备、构建和预检

两端各自执行，无需同时启动。`prepare` 以 `d822cf31` 为底创建独立 worktree。`build` 用同一份模块贯穿之后的六项。

```bash
tail_scope prepare
tail_scope build
tail_scope check
```

成功标志依次为 `FADD_PATH_PREPARE_PASSED`、`FADD_PATH_BUILD_PASSED`、`FADD_PATH_CHECK_PASSED`。预检验证环境、扩展和各端需要的 fence 配置。遇到失败时保留已收集的 `console` 和 `build` 日志。若 worktree 显示内容或版本已变，另设 TREE/BUILD，保留原目录。

## 3. 双端正式执行

202 先执行，204 在 180 秒内启动。两端用同一 JOB。运行器逐项检查实际 tail/head 角色、fence 及 benchmark 正确性，自动收集结果。

```bash
tail_scope run
```

两端均应显示 `FADD_PATH_RUN_PASSED`。发生错误保留已完成的 case 和失败日志。如果手动中断后还需补收，可执行：

```bash
tail_scope collect
```

## 4. 提供哪些日志

提供完整目录 `test/fadd_path/results/urma_fadd_tail_scope_01/urma/`，以实际 JOB 为准。包括：

- 两端 `overlay_node*.json`、`capabilities_node*.json`、构建清单及 console。
- 六项两端的 `setup.json`、`host.json`、`runtime.json`、`status.json`、`bench.log`、`api_samples.jsonl` 和 `sample_summary.jsonl`。
- 两个 probe 的 **202 `normal/tail_receiver.jsonl`** 和 **204 `normal/fadd_path.jsonl`**。文件由对应端自动生成。
- 若失败，保留预检查、构建、启动和内核日志。扩展二进制和临时编译文件无需提供。

两端结果汇齐后可离线生成摘要，无需 GPU：

```bash
"$FADD_PATH_PYTHON" test/fadd_path/analyze_tail_scope.py \
  --root "$DEST/$JOB" --output "$DEST/$JOB/summary_tail_scope"
```

还可一并提供 `summary_tail_scope/`。输出包含 `REPORT.zh-CN.md`、`decision.json`、两端性能对照、202 的逐窗接收时间、204 的逐 FADD 时间界限及来源指纹。摘要输出目录应为新目录。

## 5. 怎么判断本轮结果

先核对关闭探针对照的方向和轮间漂移，再看 `decision.json`。既有的 5% 轮内 CV、前后漂移和探针扰动检查同时应用于双端和取大值主指标。A 与 C 的扰动差还要在 1 个百分点内。代表性失败时仍保留原始测量和关闭探针的性能，阶段对比只作为带探针现象。GPU 映射时钟有单独检查。202 的差值若与两轮映射时钟最大间隔之和同量级，只报告观测读数。

| 结果 | 后续动作 |
| --- | --- |
| C 的返回后等待下界仍大，期间有大量短间隔空轮询 | 查设备何时发布 CQE、provider 何时交付；若证实是共享 JFC 队首阻塞，再验证隔离完成队列 |
| C 的返回后区间确实缩短，202 的最后 token 时间和无探针 Dispatch 也一起提前 | 继续沿通知／完成路径优化，并量化还有多少差距 |
| C 的完成明显提前，202 尾部与无探针 Dispatch 提前很少 | 优先检查后续 WRITE 和接收可见性，评估写加通知机制 |
| 返回值在 CQE 后才首次采到，区间上界仍大，或探针门槛失败 | 保留删失和采样边界，现有观测无法区分两条路径。先据日志调整探针，保持本轮范围 |

本轮记录的是本地 SDK 交付完成的时刻和接收端的本地 GPU 时间跨度。物理 CQE 就绪时刻仍未测量，两端时间戳不会跨机相减。204 的第 6 对 FADD 与 202 的最后一个 token 不是同一条请求。这两个指标的共同变化用于确定后续方向。
