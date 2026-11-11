# 下一组：关闭采集，只比较连续调用与逐样本同步

OFF结果已收到，见[核查报告](results/OFF_CONTROL_ANALYSIS.zh-CN.md)。同一事件模块关闭采集后，两端Dispatch距前后基线只有+0.12%～+0.19%，不支持大幅变化由编译进新探针本身造成。现在只补一个采样方式对照，不再重跑完整baseline/on/post，不重建GPU模块。

## 两项各测什么

| 项目 | batch | paced |
| --- | --- | --- |
| 模块 | 同一urma_events_v1 | 同一urma_events_v1 |
| GPU/CPU事件、主机时钟线程 | 全关闭 | 全关闭 |
| 预热/采样 | 5/10，丢首样本后9个有效值 | 相同 |
| Dispatch调用方式 | 原连续调用，预先创建Event，组末同步 | 每样本同步/barrier，逐样本创建Event，调用后同步 |
| layout/Combine | 原计时方式 | 相同，作为控制项 |

paced复用on的显式同步、barrier及Event创建流程，但不调用Capture、不分配/重置GPU统计、不启动时钟线程、不记录CPU timeline，也不读取事件结果。两项都保留24组原正确性测试。**本轮没有GPU阶段记录是预期行为**。

若paced在无采集时也复现204 BF16明显加速，更支持采样方式改变了两端执行状态；若batch/paced接近而旧on有偏差，更应检查采集激活、主机时钟及读回的影响。当前对照比较的是这组采样方式差异，不能将结果进一步单独归因到某一个barrier或Event创建操作。

新增的只是Python/Bash诊断入口。两端需拉取同一最新提交，使diagnostics_hash一致；ep源文件和编译参数未变，仍可复用原.so。不要修改manifest。新控制之间使用同一版诊断脚本做严格对比，不把旧on任务与新控制硬凑为同一脚本版本的三项验收。

## 1. 两端设置

202设RANK=0，204设1。MACA、私有库、额外运行库以及设备空闲条件沿用刚刚成功的环境。

```bash
cd /home/h00918771/uccl-ep-xpu-integration-new
set -euo pipefail
export URMA_DIAG_HOME=/home/h00918771
export URMA_DIAG_PYTHON=/home/h00918771/metax_dev/venv-maca-py312/bin/python
export MACA_PATH=/opt/maca-3.9.0
export URMA_DIAG_LIBRARY_DIR=/home/h00918771/metax_dev/urma_after_kernel_20261029/diag/umdk_private_618957c3/lib
# URMA_DIAG_EXTRA_LIBRARY_DIRS保留原来成功值（若有）。
RANK=0  # 204改1
TRACE=/home/h00918771/metax_dev/reference_event_build/urma_events_v1
ROOT=/home/h00918771/metax_dev/reference_event_results
DEST="$PWD/test/reference_events/results"
```

## 2. 定义运行及收集函数

```bash
run_control() {
  local step="$1" job="$2" rc=0
  bash test/reference_events/run.sh --action run --backend urma \
    --step "$step" --node-rank "$RANK" --master-addr 141.61.17.202 \
    --build-dir "$TRACE" --job "$job" --tokens 4096 \
    --output "$ROOT" --port 32880 || rc=$?
  "$URMA_DIAG_PYTHON" test/gpu_stage_recovery/collect.py "$ROOT/$job" \
    --node-rank "$RANK" --trace-build "$TRACE" --destination "$DEST/$job"
  return "$rc"
}
```

## 3. 顺序运行，只做两项

202先启动，180秒内204启动相同JOB；两端第一项成功后再运行第二项。不能并发跑两个任务。

```bash
run_control batch urma_ref_4096_batch_01
```

```bash
run_control paced urma_ref_4096_paced_01
```

两端均应有CASE_END normal rc=0、reference_control=True、REFERENCE_EVENT_COMMAND_PASSED。runtime的reference_events_version=1；setup的reference_events分别为batch/paced；GPU旧trace版本为0。reference_control.jsonl应有FP8和BF16两个执行标记，均gpu_events=false、cpu_events=false、host_clock=false。不应生成event_trace.jsonl或reference_correctness.json。

任一步失败先保留日志并停止，不自动重试，不覆盖已有JOB。预热与采样个数均很小，若出现离群值要保留；这一对照不是长期稳定性测试。

## 4. 需要提供的目录

两端分别提供以下两个完整目录，含node0/node1、console和自动收集的build日志：

```text
test/reference_events/results/urma_ref_4096_batch_01/
test/reference_events/results/urma_ref_4096_paced_01/
```

保留API原始samples与summary、reference_control.jsonl、status、setup/runtime/host、模块manifest、内核及GPU快照；无需.so、objects。不需要重传已完成的base/on/post/off。

两端齐全后也可以直接交付原始结果，由开发端汇总；可选离线命令如下，输出目录必须不存在：

```bash
"$URMA_DIAG_PYTHON" test/reference_events/summarize_cadence.py \
  --batch "$DEST/urma_ref_4096_batch_01" \
  --paced "$DEST/urma_ref_4096_paced_01" \
  --output "$DEST/urma_ref_cadence_summary_01"
```

成功标志REFERENCE_CADENCE_SUMMARY_PASSED只表示原始样本、执行模式、同模块和环境身份校验通过；cadence_controls.csv另列变化及CV，不把摘要成功自动当成性能无扰动。开发端已做模拟及回归检查，真实GPU上的新采样模式仍需本轮验证。
