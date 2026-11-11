# 只补一次on：与已完成的paced使用同一采样方式

batch/paced已完成，参见[分析](results/CADENCE_CONTROL_ANALYSIS.zh-CN.md)。逐样本同步已在关闭采集时复现204 BF16下降约16%。现在只补一次当前脚本版本的on，以已有paced为控制，完成采集自身影响的匹配检查；不重复smoke、base/post/off或batch/paced。

原on与新paced的诊断脚本版本不同，不能拿它们直接通过严格身份检查。本次仅增加tools/reference_events下的离线分析器与Markdown，不修改测量代码；这两个类型不在diagnostic_manifest的采集范围中。因此可与已有paced保持同一诊断脚本身份，GPU模块也不需重编译。分析器自身SHA会写入输出，原有来源/模块校验不放宽。

## 两端设置

202设RANK=0，204设1。保留本轮成功的额外库路径、空闲条件及内核，不重启/重载驱动。

```bash
cd /home/h00918771/uccl-ep-xpu-integration-new
set -euo pipefail
export URMA_DIAG_HOME=/home/h00918771
export URMA_DIAG_PYTHON=/home/h00918771/metax_dev/venv-maca-py312/bin/python
export MACA_PATH=/opt/maca-3.9.0
export URMA_DIAG_LIBRARY_DIR=/home/h00918771/metax_dev/urma_after_kernel_20261029/diag/umdk_private_618957c3/lib
# URMA_DIAG_EXTRA_LIBRARY_DIRS沿用本次成功环境（若有）。
RANK=0  # 204改1
TRACE=/home/h00918771/metax_dev/reference_event_build/urma_events_v1
ROOT=/home/h00918771/metax_dev/reference_event_results
DEST="$PWD/test/reference_events/results"
JOB=urma_ref_4096_matched_on_01
```

## 只运行这一个任务并收集

202先启动，180秒内204执行相同JOB，5次预热、10次采样，9个有效值，FP8/BF16及控制项不变。

```bash
rc=0
bash test/reference_events/run.sh --action run --backend urma --step trace \
  --node-rank "$RANK" --master-addr 141.61.17.202 \
  --build-dir "$TRACE" --job "$JOB" --tokens 4096 \
  --output "$ROOT" --port 32880 || rc=$?
"$URMA_DIAG_PYTHON" test/gpu_stage_recovery/collect.py "$ROOT/$JOB" \
  --node-rank "$RANK" --trace-build "$TRACE" --destination "$DEST/$JOB"
echo "MATCHED_ON_RUN_RC=$rc"
test "$rc" -eq 0
```

成功应有CASE_END normal rc=0、reference_correctness=True、reference_events=True及REFERENCE_EVENT_COMMAND_PASSED。失败保留日志停止，不自动重试，不删除离群样本。

提供test/reference_events/results/urma_ref_4096_matched_on_01完整目录，两端node0/node1、console、自动收集到的build日志均保留。必须保留event_trace.jsonl、reference_correctness.json、原始API样本/summary、status、setup/runtime/host、模块manifest、内核/GPU快照；不用.so和objects，不重传已经完成的其他实验。

## 离线匹配检查，可由开发端代做

两端文件齐全后执行，输出目录必须不存在：

```bash
"$URMA_DIAG_PYTHON" tools/reference_events/summarize_matched.py \
  --control "$DEST/urma_ref_4096_paced_01" \
  --trace "$DEST/urma_ref_4096_matched_on_01" \
  --output "$DEST/urma_ref_matched_summary_01"
```

分析器严格要求同一生产源码、诊断脚本、SDK/库、设备、boot、benchmark及模块SHA；检查原始API样本、正确性和事件。不要把旧on目录改名来满足新任务。若身份变化，保留错误，不改manifest/哈希。

matched_controls.csv按每个节点/测点列paced/on变化及CV；5%只是工程筛选，且此处没有新post控制，不构成统计显著性检验或零内部扰动证明。若通过，开始使用匹配口径的阶段结果并准备另一后端的同口径采集；若仍异常，先解释本次原始事件/离群值，不自动再跑一轮全面扫描。
