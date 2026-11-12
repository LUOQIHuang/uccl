# 下一后端：verbs同口径Normal阶段采集

2026-11-12：本流程已完成，smoke128_02、4096 paced/on两端均通过，已有[后端对照报告](results/BACKEND_MATCHED_ANALYSIS.zh-CN.md)。无需重复本套实验；以下保留完整操作步骤供复现。verbs GPU采集入口已在这轮服务器结果中验证。

URMA匹配paced/on已通过，见[结果报告](results/MATCHED_TRACE_ANALYSIS.zh-CN.md)。不再追加URMA开关扫描。以下只在已协调好的verbs内核窗口执行，不要求现在主动切核。

## 固定口径

- 当前源码verbs路径、DMA-BUF，rocep40s0f0、port1、GID3；控制接口enp33s0。
- 内核沿用已验证的6.6.0-145.3.34.165.oe2403sp3.aarch64；本入口只检查，不切换。
- 两端MACA固定为与本轮URMA相同的3.9环境，GPU0、每端1张C500；正式tokens4096、hidden7168、experts256、topk8、SM24、Dispatch chunk32、NVL chunk4。
- 只构建一个事件模块，paced和on共用同一.so。paced关闭事件/时钟，on开启；二者均逐样本同步、5次预热/10次采样，9个有效样本。
- 记录实际Torch、MACA、KMD、内核、设备和库，不能用历史MACA3.7的verbs结果直接代替本轮。若当前用户态版本不匹配，先保留情况，不宣称单一传输实现差异。

## 1. 两端设置

202设RANK=0，204设1；运行期间保持GPU及网络设备已协调空闲。EXTRA_LIBRARY_DIRS沿用该环境验证成功的值。

```bash
cd /home/h00918771/uccl-ep-xpu-integration-new
set -euo pipefail
export VERBS_DIAG_HOME=/home/h00918771
export VERBS_DIAG_PYTHON=/home/h00918771/metax_dev/venv-maca-py312/bin/python
export MACA_PATH=/opt/maca-3.9.0
RANK=0  # 204改1
GID=3
BUILD=/home/h00918771/metax_dev/reference_event_build/verbs_events_v1
ROOT=/home/h00918771/metax_dev/reference_event_results
DEST="$PWD/test/reference_events/results"
uname -r
mx-smi
```

## 2. 两端各构建事件模块一次

如果同一源码下已有这个模块且manifest校验通过，可复用；不要拿普通verbs模块或历史私有模块替代。

```bash
bash test/reference_events/run.sh --backend verbs --action build --step trace \
  --node-rank "$RANK" --job verbs_ref_build_trace_01 \
  --build-dir "$BUILD" --output "$ROOT" --gid-index "$GID"
```

成功标志VERBS_BUILD_PASSED或VERBS_BUILD_ALREADY_PASSED。脚本先compile-only precision probe及CPU模拟，再构建扩展；不启动GPU benchmark。失败保留build.log、host_tests、precision_probe和console，不继续测，不编辑manifest。

## 3. 定义运行并自动收集函数

```bash
run_verbs_case() {
  local step="$1" job="$2" tokens="$3" rc=0
  bash test/reference_events/run.sh --backend verbs --action run --step "$step" \
    --node-rank "$RANK" --master-addr 141.61.17.202 \
    --build-dir "$BUILD" --job "$job" --tokens "$tokens" \
    --output "$ROOT" --gid-index "$GID" --port 32880 || rc=$?
  "$VERBS_DIAG_PYTHON" test/gpu_stage_recovery/collect.py "$ROOT/$job" \
    --node-rank "$RANK" --trace-build "$BUILD" --destination "$DEST/$job"
  return "$rc"
}
```

## 4. 顺序运行

202先启动，180秒内204启动相同JOB；每项两端均通过后才运行下一项，不并行。

先128-token确认该后端的事件及正确性，预热1/采样3：

```bash
run_verbs_case trace verbs_ref_smoke128_01 128
```

成功需reference_correctness=True、reference_events=True及CASE_END normal rc=0；失败先收集停止，不自动继续4096。

随后只做两项正式任务：

```bash
run_verbs_case paced verbs_ref_4096_paced_01 4096
```

```bash
run_verbs_case trace verbs_ref_4096_matched_on_01 4096
```

paced应reference_control=True、无event_trace.jsonl；on应reference_correctness/reference_events=True，并有每个Dispatch的10个事件窗口。不跑LL、不调优扫描，不另做连续调用base/off/post。

## 5. 提供这些目录

提供test/reference_events/results下verbs_ref_smoke128_01、verbs_ref_4096_paced_01、verbs_ref_4096_matched_on_01完整目录。两端node0/node1、console、自动收集的build日志齐全。保留原始API样本/summary、reference_control或reference_correctness、on的event_trace、status、setup/runtime/host、模块manifest、GPU及内核快照；不需要.so和objects。

构建或最开始check失败也要保留可获得的日志；可手工执行同一collect.py，root填失败JOB，不为补文件再次启动benchmark。

两端齐全后的可选离线汇总：

```bash
"$VERBS_DIAG_PYTHON" tools/reference_events/summarize_matched.py \
  --control "$DEST/verbs_ref_4096_paced_01" \
  --trace "$DEST/verbs_ref_4096_matched_on_01" \
  --output "$DEST/verbs_ref_matched_summary_01"
```

matched_controls列采集影响和CV，其他CSV导出请求链、GPU事件和覆盖情况。输出目录需不存在。URMA和verbs分别先验收，再比较相同token/dtype和操作类型的记录；两个节点时间戳不直接相减，不把CQE当成对端GPU已消费，也不把不同内核的对照宣称为完全消除了系统栈差异。
