# 下一轮：4096-token Dispatch 打桩关闭对照

## 本轮目的及范围

上一轮正确性和覆盖通过，但详细打桩使 FP8 Dispatch 从约5.75 ms变为14.95 ms，BF16从7.97 ms变为16.22 ms，不能用这些内部时间解释原始性能差额。本轮只判断：**打桩构建在完全不启动采集时，是否仍改变性能。**

`compiled-off` 与此前的 `bypass` 不同：前者不调用 `gpu_stage_begin/end`，不增加逐样本双端 barrier、同步、统计分配或拷回；按普通基线的 CUDA Event 连续采样流程运行。设备统计指针保持初始空值，不执行探针时钟读取和统计写入。编入模块的条件分支和关闭状态的 host launch 检查仍存在，因此测到的是整个打桩构建的关闭代价，不能直接称为纯GPU指令代价。

本轮固定 Normal，4096 tokens、hidden7168、experts256、topk8、单卡；保留两种Dispatch和两种Combine控制点，不跑LL。每项预热50次、计时50次、丢弃首样本，保留49个有效样本。本轮同一时间窗口自动执行三项：

| 顺序 | 模块与状态 | 用途 |
| --- | --- | --- |
| base | 原未打桩模块，关闭采集 | 前基线 |
| off | 原打桩模块，完全不启动采集 | 测编入打桩代码的影响 |
| post | 同一未打桩模块，关闭采集 | 检查环境漂移 |

三项均使用独立进程，逐项检查正确性，失败停止。**不重跑详细trace，不切内核，不改WRITE/atomic顺序或完成策略。**URMA仍需此前可运行的diag内核；当前先测URMA。verbs也已支持此入口，暂不要求占用其测试窗口。

## 1. 两端设置环境，复用已成功的两份构建

本次只改Python/Bash诊断入口，未改GPU/CPU生产源码。可直接复用上一轮 `urma_base_v1`、`urma_trace_v1`，无需重新构建。脚本仍核对源码清单、模块SHA和运行时能力，不能通过修改manifest绕过检查。如果服务器还有其他生产源码修改，检查会拒绝复用，保留错误日志即可。

两端在拉取代码后分别执行：

```bash
cd /home/h00918771/uccl-ep-xpu-integration-new
set -euo pipefail
export URMA_DIAG_HOME=/home/h00918771
export VERBS_DIAG_HOME="$URMA_DIAG_HOME"
export URMA_DIAG_PYTHON=/home/h00918771/metax_dev/venv-maca-py312/bin/python
export VERBS_DIAG_PYTHON="$URMA_DIAG_PYTHON"
export MACA_PATH=/opt/maca-3.9.0
export URMA_DIAG_LIBRARY_DIR=/home/h00918771/metax_dev/urma_after_kernel_20261029/diag/umdk_private_618957c3/lib
# 保留上一轮成功使用的 EXTRA_LIBRARY_DIRS / LIBRARY_PATH（若有）。
# MACA 3.9.0对应刚上传结果的实际加载路径，本轮不切回历史3.7环境。

BASE=/home/h00918771/metax_dev/dispatch_diag_build/urma_base_v1
TRACE=/home/h00918771/metax_dev/dispatch_diag_build/urma_trace_v1
ROOT=/home/h00918771/metax_dev/dispatch_diag_results
DEST="$PWD/test/dispatch_diag/results"
JOB=urma_dispatch_disabled_01
RANK=0  # 202设0；204必须改为1

uname -r
mx-smi
test -f "$BASE/manifest.json"
test -f "$TRACE/manifest.json"
```

两端使用同一提交、相同JOB，且三项之间不更换运行库或重启。URMA内核应为 `6.6.0-gdr_f00955680_diag`；入口会校验。

## 2. 两端各运行一条命令

202先启动，180秒内在204启动相同命令，RANK分别为0和1：

```bash
bash test/dispatch_diag/run_compiled_off.sh \
  --backend urma --node-rank "$RANK" --master-addr 141.61.17.202 \
  --base-build "$BASE" --trace-build "$TRACE" \
  --job "$JOB" --output "$ROOT" --destination "$DEST" --port 32880
```

脚本先检查两份模块，再依次运行base、off、post，并自动收集每项日志。无需手动运行额外trace或128-token测试。正常应有三次 `CASE_END normal rc=0`，最后：

```text
DISPATCH_COMPILED_OFF_ALL_PASSED node=0 ...
```

204末尾为node=1。每个任务的status检查均应通过。off不应产生 `gpu_stages.jsonl`，也不要求 `dispatch_coverage`；这是完全关闭采集的预期行为，不能用“没有GPU统计文件”判断失败。

不覆盖已存在的node目录；如需重跑，修改JOB为 `urma_dispatch_disabled_02`，两端一致。某项失败即停止，不继续手工跳过；已启动run的失败日志也会收集。若在最开始的check失败，还可对失败JOB执行下列收集命令：

```bash
# SUFFIX按失败项填写base/off/post；原始控制台日志也位于ROOT中对应JOB的console。
SUFFIX=off
"$URMA_DIAG_PYTHON" test/gpu_stage_recovery/collect.py "$ROOT/${JOB}_${SUFFIX}" \
  --node-rank "$RANK" --base-build "$BASE" --trace-build "$TRACE" \
  --destination "$DEST/${JOB}_${SUFFIX}"
```

## 3. 两端结果齐全后汇总

在包含两端结果的机器执行一次：

```bash
"$URMA_DIAG_PYTHON" test/dispatch_diag/summarize_compiled_off.py \
  --baseline "$DEST/${JOB}_base" \
  --compiled-off "$DEST/${JOB}_off" \
  --post "$DEST/${JOB}_post" \
  --output "$DEST/${JOB}_summary"
```

成功标志 `DISPATCH_COMPILED_OFF_SUMMARY_PASSED` 只表示数据完整、一致性校验通过；具体判断看紧随其后的DECISION，以及 `compiled_off_controls.csv`。汇总覆盖两节点、FP8/BF16 Dispatch、layout和两项Combine，核对源码、编译开关、模块SHA、运行库、采样数和boot身份，拒绝混用上次旧环境结果。

| 结果 | 下一步含义 |
| --- | --- |
| base/post漂移或任一配置CV超过5% | 先检查环境，当前结果不足以区分编译代价与波动 |
| 稳定且off较前后基线均值慢超过5% | 先检查编译结果及关闭状态路径，重点看寄存器、spill、资源占用；尚不能直接认定其中某项为原因 |
| 稳定且off变化在±5%以内 | 未发现大幅关闭代价；详细采集路径及其同步节奏仍是扰动候选，下一轮精简高频探针后再验证代表性 |
| 稳定且off快超过5% | 打桩构建也可能改变调度或代码生成，仍需解释后再采内部时间 |

5%为筛选大效应的阈值，不是显著性检验，也不是URMA/verbs原始根因的判定。两种模块间的关闭状态差额包含host及device编译路径差异。此次不要求重新生成编译资源报告，先上传collector带回的现有build.log。

## 4. 需要提供的日志

只需本轮下列目录；两端node0/node1及各自console均需保留：

```text
test/dispatch_diag/results/urma_dispatch_disabled_01_base/
test/dispatch_diag/results/urma_dispatch_disabled_01_off/
test/dispatch_diag/results/urma_dispatch_disabled_01_post/
test/dispatch_diag/results/urma_dispatch_disabled_01_summary/   # 汇总完成则提供
```

自动收集包含setup、host、runtime、模块manifest、冻结benchmark、status、bench.log、API原始samples和summary、measurement boundary、内核/GPU快照、已有构建/precision probe日志。无需提供.so、objects或重新上传上一轮大体积详细trace。失败时同样提供已收集目录及未收集的check控制台日志。

开发端验证：73项Python测试通过；Bash模拟验证两后端/两节点的参数、50/50采样、自动收集、失败停止和拒绝覆盖，文档中4个Bash代码块通过语法检查。本地没有MetaX GPU，以上不等同于服务器实测通过。
