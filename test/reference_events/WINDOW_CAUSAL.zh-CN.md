# 下一步：验证204并发窗口是否放大ATOMIC完成等待

## 为什么现在做

4096-token BF16已通过打点代表性检查。末块命令发布后，202/204分别等1.922/3.578ms才被CPU读出；这段间隔超过99%的时间，软件pending=8、队首主要是尚未观察到CQE的ATOMIC。依据见[完整分析](results/urma_boundaries_01_fifo_audit/ANALYSIS.zh-CN.md)。

这已定位当前等待机制，下一步需要因果干预，而非继续增加打点。本轮仅验证：**204将窗口8改4，能否减轻提交后拥塞并改善两端真实Dispatch？** 减窗也可能减少并行度而更慢，不预设结果。

只做4项、关闭采集、使用现有run_window_ab.sh。只改变204软件未回收命令上限，202始终8；WRITE RELAX、FADD强序/fence、completion all、lane-mixed、输入和正确性检查不变。不运行额外trace/128-token/LL/强弱序矩阵。

## 1. 两端环境和现有模块

202设RANK=0，204设1；两端其余参数相同。先拉取最新代码。设备、Atomic Request/runpm、私有库与刚成功的boundaries测试一致，不切核、不修改硬件。

```bash
cd /home/h00918771/uccl-ep-xpu-integration-new
set -euo pipefail
export URMA_DIAG_HOME=/home/h00918771
export URMA_DIAG_PYTHON=/home/h00918771/metax_dev/venv-maca-py312/bin/python
export MACA_PATH=/opt/maca-3.9.0
export URMA_DIAG_LIBRARY_DIR=/home/h00918771/metax_dev/urma_after_kernel_20261029/diag/umdk_private_618957c3/lib
export URMA_DIAG_INCLUDE_DIR=/home/h00918771/metax_dev/urma_sdk_private_include
# 额外运行库沿用刚成功的boundaries设置，不混入旧MACA库。
RANK=0  # 204改1
BUILD=/home/h00918771/metax_dev/reference_event_build/urma_boundaries_v1
ROOT=/home/h00918771/metax_dev/reference_event_results
JOB=urma_window_after_boundaries_01
DEST="$PWD/test/reference_events/results"
CONSOLE="/home/h00918771/metax_dev/window_ab_console/$JOB/node$RANK"
mkdir -p "$CONSOLE"
uname -r
mx-smi
```

**复用刚完成本轮boundaries的BUILD，不需要重新编译。** 本次只增加离线分析和文档，生产源码/诊断运行入口未改变，窗口查询接口已经在该模块中。若你当时使用了不同BUILD路径，只替换为那个已成功的实际目录；不要拷贝.so或编辑manifest。

关闭采集使用legacy控制标记，仅表示不启用12流采集；同一个模块始终关闭GPU记录、CPU记录及主机时钟，A/B一致。

## 2. 两端执行同一条命令

202先启动，180秒内204启动；33080/33081端口空闲，不并行运行其他GPU任务：

```bash
bash test/reference_events/run_window_ab.sh \
  --backend urma --part performance --node-rank "$RANK" \
  --master-addr 141.61.17.202 --port 33080 \
  --build-dir "$BUILD" --job "$JOB" --output "$ROOT" --destination "$DEST" \
  2>&1 | tee "$CONSOLE/performance.log"
```

程序依次运行A→B→B→A：

| 任务后缀 | 202窗口 | 204窗口 | 采集 |
| --- | ---: | ---: | --- |
| r01_default | 8 | 8 | 关闭 |
| r01_peer4 | 8 | 4 | 关闭 |
| r02_peer4 | 8 | 4 | 关闭 |
| r02_default | 8 | 8 | 关闭 |

两端都传相同计划，由node-rank决定本地值，不要手动export UCCL_IB_MAX_INFLIGHT_NORMAL。每项先check再run；预检查验证实际normal_max_inflight，不能只相信环境变量已设置。5次预热、10次采样、丢首值后9个有效样本。固定4096 tokens、hidden7168、experts256、topk8、SM24、Dispatch chunk32、NVL chunk4、每端一张GPU0，保留FP8/BF16及原有其他API和真实正确性检查。

每项CASE_END normal rc=0、全部checks=true，最终WINDOW_AB_PASSED。失败会收集已有文件并停止；提供失败日志，不为补文件重跑。已有同名结果不覆盖，确需重跑时用新JOB。

## 3. 只提供这些结果

DEST下以下4个完整目录，各含node0/node1、自动收集的console/build/manifest：

```text
urma_window_after_boundaries_01_r01_default/
urma_window_after_boundaries_01_r01_peer4/
urma_window_after_boundaries_01_r02_peer4/
urma_window_after_boundaries_01_r02_default/
```

另提供CONSOLE的performance.log。保留原始api_samples、sample_summary、reference_control、status、bench.log、setup/preflight/runtime/host及快照。无需.so/objects；本轮没有新构建，不要求额外构建日志。失败只提供当时已有目录。

可选离线汇总，不需GPU：

```bash
"$URMA_DIAG_PYTHON" tools/reference_events/summarize_window.py \
  --root "$DEST" --job "$JOB" --output "$DEST/${JOB}_summary"
```

## 怎么决定是否继续

主指标为BF16每个对应样本max(node0,node1)的均值，并检查各端均值/CV；它不是跨机器时钟测得的全局makespan。

- 两轮B均净改善≥5%，相关CV≤5%、正确性通过：支持当前深度放大等待、减窗有净收益；再根据结果决定是否补采集验证目标长尾同步收缩，不预先运行trace。
- 两轮变化绝对值均<3%：不支持8→4是主要净优化点，保留8，不追加2/6/16扫描。
- 整体变慢：不能因某段post→CQE可能变短就声称优化有效；等待可能移到了提交前，保留8。
- 3%～5%、方向相反或CV超限：保留为不充分证据，先分析已有样本，不自动重复矩阵。

本实验改变URMA当前路径的并发，不单独证明URMA-vs-verbs全部差额。无论结果如何，都不会推翻本轮已经重建出的FIFO满额等待事实；它回答的是这种深度能否通过低风险改动得到净改善。
