# URMA Normal：WRITE强序/弱序单变量对照

目的：直接判断真实Normal的Dispatch在WRITE使用STRONG或RELAX时，整体时延是否改变。不是比较Normal与LL，也不是把FADD改成弱序。FADD仍为STRONG，原请求fence保持，完成策略固定all；不改变GPU程序、FIFO、lane、批量提交策略、缓冲区、quiet/barrier。

本轮仅在已支持URMA GPU路径的`6.6.0-gdr_f00955680_diag`内核及已协调好的设备环境运行。不在当前发行版内核上跳过检查运行，也不由脚本切内核、装驱动或修改PCI配置。

## 1. 测试内容与成功标准

- 同一个新构建模块，通过运行时开关切换`--normal-write-order relax|strong`。默认生产行为仍是Normal WRITE弱序、LL WRITE强序。
- 三轮独立配对：r01弱→强，r02强→弱，r03弱→强，共6个双端进程任务。
- 保留原有正确性检查及五个固定计时点：layout、FP8/BF16 Dispatch chunk32、Combine chunk12/16。重点比较两个Dispatch，其他点作为控制；不额外扫描参数。
- tokens4096、hidden7168、experts256、topk8、SM24、NVL chunk4。每点预热50次、计时50次、丢弃首个，49个有效样本。
- API-only，不启动Kineto、GPU打桩、CPU阶段统计、逐请求时间线或独立GPU observer微测。
- 两端所有任务必须正确性通过、运行时策略与请求一致；任何错误停止，不收集失败样本作为性能基线。

强序可能因等待前序事务而增加耗时，也可能在特定实现/负载下改善执行节奏；本轮不预设哪种更快。即使性能改善，也只说明WRITE排序策略对真实程序有影响，不能独立证明具体哪个内部等待点是根因。

## 2. 两端环境设置

两端拉取同一提交。202设置RANK=0，204设置RANK=1。保持同一套运行库到全部A/B结束，不在六个任务之间切换MACA。

```bash
cd /home/h00918771/uccl-ep-xpu-integration-new
set -euo pipefail
export URMA_DIAG_HOME=/home/h00918771
export URMA_DIAG_PYTHON=/home/h00918771/metax_dev/venv-maca-py312/bin/python
export URMA_DIAG_LIBRARY_DIR=/home/h00918771/metax_dev/urma_after_kernel_20261029/diag/umdk_private_618957c3/lib
export URMA_DIAG_INCLUDE_DIR=/home/h00918771/metax_dev/urma_sdk_private_include
export MACA_PATH=/opt/maca-3.7.0
# 沿用此前已通过检查的URMA_DIAG_EXTRA_LIBRARY_DIRS（若有）。
RANK=0  # 204改为1
JOB=urma_normal_order_01
BUILD=/home/h00918771/metax_dev/urma_mode_parity_build/normal_order_v1
ROOT=/home/h00918771/metax_dev/urma_normal_order_results/$JOB
CONSOLE="$ROOT/${JOB}_console/node$RANK"
PY="$URMA_DIAG_PYTHON"
mkdir -p "$CONSOLE"
test "$(uname -r)" = 6.6.0-gdr_f00955680_diag
test -x "$PY"
test -s "$URMA_DIAG_INCLUDE_DIR/urma_api.h"
test -d "$URMA_DIAG_LIBRARY_DIR"
mx-smi
df -h / "$URMA_DIAG_HOME"
```

上述SDK/库目录若与本机实际不同，修改为此前成功使用的真实目录，两端对应一致。MACA3.7为历史URMA通过的配方；如果服务器只能使用其他MACA版本，先固定两端实际可用版本、完成下方检查，不在A/B中混用版本，不把其结果直接并入历史3.7基线。本轮保存实际加载的MACA运行库哈希，不能只凭MACA_PATH声明判断。

## 3. 两端各构建一次；不需要双端同时开始

旧模块没有Normal顺序开关，需要一次新构建，两种策略共用这一个模块。使用新的BUILD目录；不修改旧manifest，不复用缺少能力的旧.so。下面设置单编译任务，不自动进行重试或完整重建。

```bash
bash test/urma_mode_parity/run.sh --action build \
  --node-rank "$RANK" --job "${JOB}_build" --build-dir "$BUILD" \
  --build-jobs 1 \
  2>&1 | tee "$CONSOLE/build.log"
```

先运行precision probe和CPU模拟检查，再进行扩展编译；成功标志`BUILD_PASSED`。同输入构建已完成时显示`BUILD_ALREADY_PASSED`。之前完整编译曾发生中断：若再次失败，停止并保留console、BUILD中的build.log、host_tests.log、build_request.json、precision_probe相关日志，不反复重编译。开发端未验证服务器GPU编译和真实私有SDK ABI。

## 4. 两端各自检查两种策略

```bash
bash test/urma_mode_parity/run_normal_order_ab.sh --action check \
  --node-rank "$RANK" --job "$JOB" --build-dir "$BUILD" --result-root "$ROOT"
```

无需同时启动。每个策略的导入结果应显示相应`normal_write_order`，同时`ll_write_order=strong`、`normal_completion=all`、`gpu_stage_version=0`；两次`MODE_PARITY_CHECK_PASSED`后显示`NORMAL_ORDER_CHECK_PASSED`。还会验证既有URMA硬件、Atomic Request、runpm和私有库条件。检查不等于GPU benchmark正确性已通过。

若出现`NORMAL_WRITE_ORDER_DIAGNOSTICS_REQUIRES_REBUILD`，说明加载模块不具备相应开关能力，不允许直接把旧模块用于本轮比较。其他环境检查失败同样先停止，提供check日志。

## 5. 双端运行六个任务

202先启动，180秒内在204启动；两端JOB、端口、重复次数一致。32780/32781需可用。

```bash
bash test/urma_mode_parity/run_normal_order_ab.sh --action run \
  --node-rank "$RANK" --master-addr 141.61.17.202 \
  --job "$JOB" --build-dir "$BUILD" --result-root "$ROOT" \
  --port 32780 --repeats 3
```

任务名为`${JOB}_r01_relax`、`${JOB}_r01_strong`等；r02执行次序相反。入口先重新检查两种策略，随后只运行API-only Normal；不再次编译。各任务应`CASE_END normal rc=0`，两端最后显示`NORMAL_ORDER_ALL_PASSED`。有任何失败就停止并收集日志，重跑使用新JOB和ROOT，不覆盖原始数据。

极短窗口可两端都改`--repeats 1`，仅验证能否跑通；不足以形成稳定性能结论，后面的汇总也需相应使用`--repeats 1`。正式结果默认三轮。

## 6. 收集结果和需要提供的日志

每端执行一次；收集程序保留已有相同文件，遇到不同内容冲突会拒绝覆盖。

```bash
bash test/urma_mode_parity/run.sh --action collect --collect-suite \
  --node-rank "$RANK" --job "$JOB" --build-dir "$BUILD" --output "$ROOT"
```

提供仓库下`test/urma_mode_parity/results/$JOB/`完整目录，包含六个任务各自node0/node1及`${JOB}_console/node0`、`node1`：

- setup、preflight、build_manifest、host、runtime、冻结的normal.py/low_latency.py。
- normal/下的bench.log、status.json、api_samples.jsonl、sample_summary.jsonl、metrics.jsonl、phases.jsonl、GPU/内核/端口快照、measurement_boundary.json。
- console中的build.log、check_relax.log、check_strong.log和六个任务日志。

不需要.so、objects和cache。若构建失败还没有manifest，提供第3节列出的构建诊断文件即可，不为补结果反复运行。

## 7. 两端数据齐全后汇总

```bash
DEST="$PWD/test/urma_mode_parity/results/$JOB"
"$PY" test/urma_mode_parity/summarize_normal_order.py \
  --root "$DEST" --job "$JOB" --repeats 3 --output "$DEST/summary"
```

成功标志`NORMAL_ORDER_SUMMARY_PASSED`。缺少任一端、错误轮次、模块/构建/SDK/实际MACA库/boot/配置变化、策略未生效、采样错误均拒绝汇总。

- order_runs.csv：逐节点、逐轮、逐点49个样本的均值、CV和分位数。
- order_comparison.csv：两种策略三轮均值的中位数、范围，以及逐轮配对变化；`strong_change_pct=(strong/relax-1)*100`，负数表示强序更快。
- context.json：固定输入、运行环境和比较边界。

不合并两节点当成六次重复。判断时同时看两个Dispatch测点、两节点方向、配对差值及轮间范围；范围重叠、方向不一致时不能宣称稳定优势。字段只是描述性统计，不是显著性检验。若无清晰改善，就降低WRITE强弱序是主要原因的优先级；本轮不修改默认策略。

若以后要单独测LL，继续使用[LL排序A/B文档](ORDER_AB.zh-CN.md)的入口和独立JOB，不能把本轮Normal结果外推为LL结论。
