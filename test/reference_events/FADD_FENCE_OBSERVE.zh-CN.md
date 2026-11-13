# Normal下一步：确认fence收益是否对应通知推进/FIFO回收改善

**首轮遇到阻断，暂不重跑下文六项。**请先按[本轮结果与只读补收集步骤](results/urma_fadd_fence_observe_01_ANALYSIS.zh-CN.md)收取已有all_on_trace故障日志。A基线通过，但post出现URMA完成错误，B被barrier残留拦截，尚无完整阶段对照。

## 目的与停止边界

上一轮204数据FADD fence off的ABBA已取得BF16净收益3.35%/3.52%，平均约278μs；不重复原ABBA。已有fence-on trace中，204 WRITE提交结束→通知提交结束平均394.39μs，其中388.52μs发生在CPU取到通知命令之前，取到后只约5.87μs。

本轮只回答：关闭204数据FADD fence后，是否同时缩短通知读取前的延迟、WRITE/FADD完成观测间隔，以及末块发布后的FIFO回收背压？既有间隔不能直接叫CPU计算或硬件atomic耗时，也不能把不同重叠区间相加成总Dispatch。

继续沿用A=两端on，B=仅204 off。FADD仍STRONG、WRITE RELAX、窗口8、completion all、lane-mixed；barrier/recovery及LL不变。作用范围仍是204全部Normal数据ATOMIC，包括通知、反馈及Combine相关请求，不是单独一条tail通知。

**不修改生产C++/CUDA代码，不加新打点，复用已验证的12点稀疏boundary profile。**已有fadd_fence_v1模块具备reference_boundary_version=1及fence能力，源码未变时可复用，无需重编。更新仅涉及运行/分析脚本和文档。

## 任务量

- 一次128-token开启采集的B配置验证：验证边界结构/采集及实际fence生效，不重复衡量小形状性能。
- 六项正式4096-token任务：A关闭采集→A采集→A关闭采集，再B关闭采集→B采集→B关闭采集。
- **只有两次正式任务开启采集**；四个关闭采集控制用于判断约3.4%收益是否被观察器改变。这不是新的ABBA，净性能主证据仍为上一轮。
- 每项真实Normal正确性检查保留；正式5次预热/10次采样/丢首值，固定hidden7168、experts256、topk8、SM24、chunk32，其他API是原程序附带检查，主分析只看BF16。
- 不扫描WRITE强弱、窗口、提交调度，不增加LL。失败收集后停止，不自动重跑或采用更密集打点。

## 1. 两端设置

拉取本轮代码后，在此前成功的URMA环境运行。202设RANK=0，204设1；保持私有库、MACA和额外运行库与上一轮一致：

```bash
cd /home/h00918771/uccl-ep-xpu-integration-new
set -euo pipefail
export URMA_DIAG_HOME=/home/h00918771
export URMA_DIAG_PYTHON=/home/h00918771/metax_dev/venv-maca-py312/bin/python
export MACA_PATH=/opt/maca-3.9.0
export URMA_DIAG_LIBRARY_DIR=/home/h00918771/metax_dev/urma_after_kernel_20261029/diag/umdk_private_618957c3/lib
export URMA_DIAG_INCLUDE_DIR=/home/h00918771/metax_dev/urma_sdk_private_include
RANK=0  # 204改为1
BUILD=/home/h00918771/metax_dev/reference_event_build/urma_fadd_fence_v1
ROOT=/home/h00918771/metax_dev/reference_event_results
JOB=urma_fadd_fence_observe_01
DEST="$PWD/test/reference_events/results"
CONSOLE="/home/h00918771/metax_dev/fadd_fence_console/$JOB/node$RANK"
mkdir -p "$CONSOLE"
uname -r
mx-smi
```

预期内核6.6.0-gdr_f00955680_diag。先确认BUILD是本端上一轮成功的模块目录，不复制另一端.so，不手工编辑manifest，不手工设置fence环境变量。脚本根据rank设置202 on、204按计划on/off，并检查真实transport日志。

**已有BUILD直接下一步，不需要再build。**若本端模块不存在，才按此前构建流程生成一个新目录并修改BUILD变量；两端六项始终使用各自同一个模块：

```bash
# 仅BUILD缺失时执行；若提示SOURCE_CHANGED，换新BUILD目录重建，不改manifest。
bash test/reference_events/run.sh --backend urma --action build --step trace \
  --node-rank "$RANK" --build-dir "$BUILD" --job "${JOB}_build" --output "$ROOT" \
  2>&1 | tee "$CONSOLE/build.log"
```

## 2. 128-token采集验证

两端均执行，202先启动，180秒内204启动；33380/33381空闲：

```bash
bash test/reference_events/run_fadd_fence.sh --part observe-smoke \
  --node-rank "$RANK" --master-addr 141.61.17.202 --port 33380 \
  --build-dir "$BUILD" --job "$JOB" --output "$ROOT" --destination "$DEST" \
  2>&1 | tee "$CONSOLE/observe_smoke.log"
```

两端均需CASE_END normal rc=0、全部checks=true，最后FADD_FENCE_SUITE_PASSED part=observe-smoke。检查runtime中reference_boundary_version=1、reference_profile=boundaries、normal_fadd_fence_version=1，202 on/204 off；reference_events与reference_correctness等采集/正确性检查通过。128-token允许第6块对应点无记录，正式4096要求完整覆盖。

普通smoke/performance的旧结果不能充当本轮observe-smoke。正式运行门禁会复核本端采集smoke的模块SHA、boot、profile与fence，防止运行中切核或换模块。

## 3. 六项正式任务

两端采集smoke均通过后，两端执行：

```bash
bash test/reference_events/run_fadd_fence.sh --part observe \
  --node-rank "$RANK" --master-addr 141.61.17.202 --port 33380 \
  --build-dir "$BUILD" --job "$JOB" --output "$ROOT" --destination "$DEST" \
  2>&1 | tee "$CONSOLE/observe.log"
```

| 后缀 | fence计划 | 采集 |
| --- | --- | --- |
| all_on_paced | 202 on、204 on | 关闭 |
| all_on_trace | 202 on、204 on | 开启 |
| all_on_post | 202 on、204 on | 关闭 |
| peer_off_paced | 202 on、204 off | 关闭 |
| peer_off_trace | 202 on、204 off | 开启 |
| peer_off_post | 202 on、204 off | 关闭 |

每项check→run→收集，失败即停、不覆盖结果。最后FADD_FENCE_SUITE_PASSED part=observe。若某端失败，另一端可能因失联退出，提供两端已有日志，不清理他人进程，不自动重跑。

## 4. 需要提供的日志

DEST下以下七个完整目录，均保留node0/node1，以及自动收集的console/build元数据：

```text
urma_fadd_fence_observe_01_observe_smoke128/
urma_fadd_fence_observe_01_all_on_paced/
urma_fadd_fence_observe_01_all_on_trace/
urma_fadd_fence_observe_01_all_on_post/
urma_fadd_fence_observe_01_peer_off_paced/
urma_fadd_fence_observe_01_peer_off_trace/
urma_fadd_fence_observe_01_peer_off_post/
```

另保留CONSOLE下observe_smoke.log、observe.log及发生构建时的build.log。不要仅提供均值截图，也不要删除trace、clock质量、reference_correctness、status或原始样本。无需.so/objects。

两端齐全后可选离线汇总，不使用GPU；失败提供日志即可，不为分析失败重测benchmark：

```bash
"$URMA_DIAG_PYTHON" tools/reference_events/summarize_fence_observation.py \
  --root "$DEST" --job "$JOB" --output "$DEST/${JOB}_summary"
```

## 如何判断是否前进

工具先验证同源码、模块、boot、库、采样口径，仅允许指定fence变化。输出：

- bf16_api.csv：两端对应样本较大值均值，列出关闭/开启采集时A/B净差。
- request_intervals.csv：WRITE post_end→通知CPU读取→解码/准备/提交的均值，以及WRITE/FADD完成观测间隔的中位数。均值拆分可相加，独立中位数不能相加。
- all_on/、peer_off/：同位置GPU边界与CPU第6块/末块事件，以及每端BF16采集扰动和clock质量。
- all_on_fifo/、peer_off_fifo/：通过对应控制门槛后才生成，查看BF16末块发布→CPU读取、额度满占比、队首ATOMIC尚无完成观测的占比。占比下降不是必要条件：等待绝对时长可缩短而占比仍接近100%。
- decision.json：是否具备机制对照资格；不是唯一根因的自动判定。

预先固定工程门槛：每计划BF16两端前后漂移、采集相对控制变化、CV均≤5%，clock偏移检查通过；两组关闭采集A/B比较均改善至少2%；开启采集A/B效应与关闭采集平均效应相差≤1个百分点。这些是观察器/收益复现门槛，不是显著性检验。

因为已有收益仅3.4%，不能只满足5%的单项扰动门槛就声称观察器无影响。若额外差分门槛不通过，仍导出原始记录但不归因，不自动再加测试。

若“净收益仍在、通知读取前绝对等待及对应FIFO阻塞时长同步缩短”，支持fence与回收背压的机制联系；仍需区分通知和反馈ATOMIC，不能宣布唯一根因。若净收益仍在而这些区间不变，则应检查其他边界变化，停止沿该链条作强因果推断。若收益未复现或trace不可靠，保持上一轮约3.43%的有限结论及默认on。

已有boundary汇总可能因附带FP8扰动显示全局usable=false；本轮主分析按两端BF16单独验收，FP8不通过不能遮蔽BF16，但也不能借BF16通过解释FP8。

## 开发机验证范围

主机测试覆盖参数组合、旧配置隔离、采集smoke门禁、六项顺序、失败收集/停止，以及汇总门槛；完整汇总另用历史trace复制并合成配置/API差异作离线流程验证，**不是新增GPU实验数据**。本轮未修改生产算子或GPU代码；实机采集的正确性和代表性仍由上述门禁检验。
