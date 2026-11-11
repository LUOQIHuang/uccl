# 下一项只测：事件模块关闭采集，4096 tokens

已有128验证和4096的base/trace/post全部完成，不重复运行。完整复核见[结果报告](results/FORMAL4096_ANALYSIS.zh-CN.md)。本项不是继续找URMA/verbs差额，而是用最少一项对照弄清现有测量差异，避免拿受扰动数据继续归因。

## 目的与边界

204 BF16的trace比前后基线快15.64%，202 FP8存在8.85ms离群值；其余两项变化约2%–3%。前后基线稳定，不能用整体漂移解释。当前还混合了编译进探针、激活采集、逐样本同步/barrier等运行节奏变化。

本项加载同一urma_events_v1模块，但`--step off`使新GPU事件、CPU时间轴和主机时钟线程全部关闭，采样采用原base/post的连续节奏。原有run.sh及driver已经支持，**不修改代码、不重新编译**，没有新测试脚本需要部署。

- off也稳定偏移：优先检查事件构建本身及生成代码，不能只归因于主机时钟线程。
- off回到base/post附近：更支持激活采集或逐样本节奏导致变化；仍需后续匹配节奏控制才能区分两者。
- off本身波动大或运行失败：保留原样结果，不自动重试，不删除不利样本。

## 两端执行

保持成功实验时的内核、私有库、MACA及额外库设置。202设RANK=0，204设1；两端保持同一源码/测试脚本，设备已协调空闲。不需要驱动重载、重启或清理共享任务。

```bash
cd /home/h00918771/uccl-ep-xpu-integration-new
set -euo pipefail
export URMA_DIAG_HOME=/home/h00918771
export URMA_DIAG_PYTHON=/home/h00918771/metax_dev/venv-maca-py312/bin/python
export MACA_PATH=/opt/maca-3.9.0
export URMA_DIAG_LIBRARY_DIR=/home/h00918771/metax_dev/urma_after_kernel_20261029/diag/umdk_private_618957c3/lib
# URMA_DIAG_EXTRA_LIBRARY_DIRS保留本次成功环境的原值（若有）。
RANK=0  # 204改1
BASE=/home/h00918771/metax_dev/reference_event_build/urma_base_v1
TRACE=/home/h00918771/metax_dev/reference_event_build/urma_events_v1
ROOT=/home/h00918771/metax_dev/reference_event_results
DEST="$PWD/test/reference_events/results"
JOB=urma_ref_4096_off_01
```

202先启动，180秒内204执行相同JOB。只跑这一个任务；预热50、采样50、丢首样本，49个有效样本；五个原固定API点和正确性检查不变。

```bash
rc=0
bash test/reference_events/run.sh --backend urma --action run --step off \
  --node-rank "$RANK" --master-addr 141.61.17.202 \
  --build-dir "$TRACE" --job "$JOB" --tokens 4096 \
  --output "$ROOT" --port 32880 || rc=$?
"$URMA_DIAG_PYTHON" test/gpu_stage_recovery/collect.py "$ROOT/$JOB" \
  --node-rank "$RANK" --base-build "$BASE" --trace-build "$TRACE" \
  --destination "$DEST/$JOB"
echo "OFF_CONTROL_RUN_RC=$rc"
test "$rc" -eq 0
```

成功应有CASE_END normal rc=0和REFERENCE_EVENT_COMMAND_PASSED。runtime的reference_events_version仍为1（说明确实加载事件构建），setup的reference_events为off，gpu_stage_version=0；本轮不应生成event_trace.jsonl或reference_correctness.json，已有原正确性检查仍需通过。不要把没有事件文件当成本轮失败。

## 需要提供什么

提供test/reference_events/results/urma_ref_4096_off_01完整目录，两端node0/node1、console、自动收集到的build日志均保留。重点是API原始samples及summary、status、setup/runtime/host、模块manifest、内核快照；无需.so或objects。失败也提供同一目录。

已有基线、trace、post不用重传或重跑。开发侧用现有case_data(reference_events='off')核对身份与原始样本，再与相同节点base/post参考比较。本次仅新增Markdown文档，不改变diagnostic_manifest包含的脚本，因此不会因为阅读这份文档而导致既有身份失配。
