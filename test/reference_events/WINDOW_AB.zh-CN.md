# 下一轮：只改变204发送端的Normal未回收命令窗口

**4096-token边界测试已完成，现有数据支持恢复单变量窗口干预。**新一轮直接按[URMA窗口因果测试操作文档](WINDOW_CAUSAL.zh-CN.md)执行：复用已成功的boundaries模块，只跑关闭采集的ABBA四项，无需重编译。下文保留原双后端完整协议；暂不执行可选trace或新增verbs任务。

## 要回答什么，以及为什么测

最终目标是定位并减少URMA Normal Dispatch相对verbs的性能损失。这轮只回答：**204向202发送过程中，并发挂起的命令是否带来了有净损失的等待放大？**不继续混合提交实验，不改变WRITE/FADD/fence/完成语义，不增加GPU打点。

已对现有BF16日志完成离线定位，每端九个有效窗口、1188条大块WRITE：

- 204 URMA最慢5%的60条WRITE中，59条是各(Proxy, ring, window)的第6次大WRITE，而非全局第6个请求。
- 第6次WRITE共108条，其post→CQE中位数verbs 2.224ms、URMA 3.492ms，P95为2.315/3.709ms。
- 两后端第6次WRITE前，重建的未回收命令数中位数均为7，已提交但尚未观察到完成的原子请求中位数均为4。并非URMA额外发了更多原子；需要验证它在相同并发深度下是否等待放大得更明显。
- Normal源码默认允许每ring最多8条未回收命令；这包括WRITE和ATOMIC，不是硬件SQ深度。观察重建只覆盖完整业务链，是下界，不将它宣称成硬件内部队列长度。

复算数据在[window_audit_01](results/window_audit_01)，位置统计见payload_positions.csv，最慢请求身份在top5_payload_requests.csv。这些是选择干预位置的依据，不是已经证明队列根因。

## 唯一干预与判定

| 计划 | 202/node0 | 204/node1 |
| --- | ---: | ---: |
| default：A | 每ring上限8 | 每ring上限8 |
| peer4：B | 每ring上限8 | 每ring上限4 |

通过已有UCCL_IB_MAX_INFLIGHT_NORMAL生效。新模块只增加读取实际生效值的查询接口，没有修改GPU kernel、Proxy调度或算子实现。两端都传同一个--normal-window-plan，脚本按node-rank选择本地值，不能自行给两端传不同计划。

保持Normal WRITE RELAX、FADD强序及原fence、completion all、lane-mixed默认提交、FIFO、原输入和正确性检查。不要同时运行writes-first或强序开关。

仅4个性能任务，顺序固定ABBA：r01_default→r01_peer4→r02_peer4→r02_default。每点5次预热/10次采样，丢首值，9个有效样本；paced逐样本同步，采集和主机时钟关闭。tokens4096、hidden7168、experts256、topk8、SM24、Dispatch chunk32、NVL chunk4、每端GPU0一张卡不变。

主要看BF16每个样本max(node0,node1)的均值，辅以各端均值/CV和FP8、Combine。它不是跨机时钟测得的全局makespan。

| 结果 | 结论与后续动作 |
| --- | --- |
| URMA两轮B均改善至少5%，各端及配对指标CV≤5%，正确性通过 | 支持204窗口对净性能有实质影响；才补2个已有trace任务，查第6块WRITE长尾、202远端可见跨度是否一起缩短 |
| 提交→CQE变短，但整体不变或更慢 | 等待可能移到了提交前/生产端；不作为有效优化，不以P95降低声称解决回退 |
| 两轮整体变化绝对值均小于3% | 本负载未支持该8→4干预是主要净优化点；保留8，不追加2/6/16等扫描 |
| 劣化、方向相反、CV超标或正确性失败 | 不采用，保留已有日志，先审查，不自动重跑矩阵 |

同样的4项在verbs与URMA各运行一次，比较它们对同一窗口变化的响应。URMA明显受益而verbs没有相同收益，才支持后端间的窗口敏感性差异；两者都受益，则更像共享配置效应，不能归为URMA独有根因。

**当前若只能跑RDMA，就先运行verbs这4项，保存窗口响应参照；URMA窗口可用后运行相同4项。**不要求为本实验主动切核。当前verbs结果本身不能证明URMA窗口干预的效果；若URMA当前可用，可先跑URMA。新增目的为因果响应，不是再次复现一遍旧baseline。

## 1. 两端设置环境

202使用RANK=0，204使用1。选择当前能运行的一个BACKEND，两端必须相同：

```bash
cd /home/h00918771/uccl-ep-xpu-integration-new
set -euo pipefail
BACKEND=verbs  # URMA窗口时改成urma
RANK=0         # 204改成1
export VERBS_DIAG_HOME=/home/h00918771
export VERBS_DIAG_PYTHON=/home/h00918771/metax_dev/venv-maca-py312/bin/python
export MACA_PATH=/opt/maca-3.9.0
export URMA_DIAG_HOME="$VERBS_DIAG_HOME"
export URMA_DIAG_PYTHON="$VERBS_DIAG_PYTHON"
if [ "$BACKEND" = urma ]; then
  export URMA_DIAG_LIBRARY_DIR=/home/h00918771/metax_dev/urma_after_kernel_20261029/diag/umdk_private_618957c3/lib
  export URMA_DIAG_INCLUDE_DIR=/home/h00918771/metax_dev/urma_sdk_private_include
fi
# 沿用该后端已验证成功的额外运行库设置，不混入另一版本MACA库。
BUILD="/home/h00918771/metax_dev/reference_event_build/${BACKEND}_window_v1"
ROOT=/home/h00918771/metax_dev/reference_event_results
JOB="${BACKEND}_window_ab_01"
DEST="$PWD/test/reference_events/results"
CONSOLE="/home/h00918771/metax_dev/window_ab_console/$JOB/node$RANK"
mkdir -p "$CONSOLE"
uname -r
mx-smi
```

目的：固定后端、节点及环境。verbs预期内核6.6.0-145.3.34.165.oe2403sp3.aarch64、rocep40s0f0/GID3/DMA-BUF；URMA预期6.6.0-gdr_f00955680_diag、udmac0d1e2及之前成功的Atomic Request/runpm/私有库配置。两后端沿用MACA3.9.0.29、KMD3.10.14。脚本不会切核或清理他人进程；运行前已协调设备空闲。

## 2. 每端为当前后端构建一次

查询实际窗口值需要本次新增接口，使用新的BUILD，不能复用旧events_v1或编辑manifest。A/B及可选trace共用这个新模块：

```bash
bash test/reference_events/run.sh --backend "$BACKEND" --action build --step trace \
  --node-rank "$RANK" --job "${JOB}_build" --build-dir "$BUILD" --output "$ROOT" \
  2>&1 | tee "$CONSOLE/build.log"
```

目的：确保实际窗口可核验，避免变量写了但程序没用。成功为VERBS_BUILD_PASSED/BUILD_PASSED或对应ALREADY_PASSED；只构建与运行主机检查，不启动benchmark。

失败时保留CONSOLE及BUILD下build.log、host_tests.log、request/manifest、precision_probe日志；不要为补齐文件重新启动benchmark，不需要.so或objects。

## 3. 执行4项双端性能对照

202先启动，180秒内204启动相同命令，仅RANK不同。默认33080/33081端口需空闲，不并行运行其他任务：

```bash
bash test/reference_events/run_window_ab.sh \
  --backend "$BACKEND" --part performance --node-rank "$RANK" \
  --master-addr 141.61.17.202 --port 33080 --gid-index 3 \
  --build-dir "$BUILD" --job "$JOB" --output "$ROOT" --destination "$DEST" \
  2>&1 | tee "$CONSOLE/performance.log"
```

目的：只观察204窗口变化是否降低真实Dispatch净耗时。每项check→run→自动收集；新runtime字段normal_max_inflight应为：default两端8，peer4仅204为4。预检查会拒绝缺接口或数值不一致的模块。

全部成功标志WINDOW_AB_PASSED；每项CASE_END normal rc=0、原正确性检查通过。任一失败收集已有日志后停止，不会继续下一项。已有同名结果会拒绝，重跑需新JOB。

## 4. 提供这些完整目录

DEST下当前后端的4项，两端node0/node1、console、build日志齐全：

```text
verbs_window_ab_01_r01_default/
verbs_window_ab_01_r01_peer4/
verbs_window_ab_01_r02_peer4/
verbs_window_ab_01_r02_default/
```

URMA时前缀改为urma。脚本自动收集原始api_samples、sample_summary、reference_control、bench正确性日志、status、setup/runtime/host、manifest及GPU/kernel快照；另提供CONSOLE中的build.log/performance.log。失败也保留当次可获得目录，不只提供均值截图。

目的：核实两端同一次调用、有效窗口和模块/环境一致，不依赖截图或单端变快来判断。

两端目录齐全后的离线汇总，不需GPU，输出目录须不存在：

```bash
"$VERBS_DIAG_PYTHON" tools/reference_events/summarize_window.py \
  --root "$DEST" --job "$JOB" --output "$DEST/${JOB}_summary"
```

提供生成的per_node、paired_calls、rounds、decision、context。该工具按预先约定的工程阈值分类，不是统计显著性检验，不自动修改默认配置。

## 5. 只在性能支持方向后追加两个trace

先不运行本节。如果URMA的两轮净改善符合门槛，或需要核查实际窗口如何改变请求推进，再执行；不重跑前四项，也不增加GPU打点：

```bash
bash test/reference_events/run_window_ab.sh \
  --backend "$BACKEND" --part trace --node-rank "$RANK" \
  --master-addr 141.61.17.202 --port 33080 --gid-index 3 \
  --build-dir "$BUILD" --job "$JOB" --output "$ROOT" --destination "$DEST" \
  2>&1 | tee "$CONSOLE/trace.log"
```

新增JOB_trace_default、JOB_trace_peer4两目录，也要两端。与各自r02 paced验收后，再比较第6块WRITE、整体及可见性记录：

```bash
for PLAN in default peer4; do
  "$VERBS_DIAG_PYTHON" tools/reference_events/summarize_matched.py \
    --control "$DEST/${JOB}_r02_${PLAN}" --trace "$DEST/${JOB}_trace_${PLAN}" \
    --output "$DEST/${JOB}_trace_${PLAN}_summary"
done
```

目的：验证性能改善是否伴随目标异常收缩。采集扰动超限时，前四项的关闭采集性能仍保留，不让观察器失败阻断性能判断。
