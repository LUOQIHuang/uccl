# 历史取证结束：新JOB先验证A的4096稀疏采集

目的：只解决“默认fence开启时，4096-token的稀疏采集及后控制能否完成”。这一步不尝试新的优化，不启动完整A/B六项，不再补收旧故障。

最新归档找回成功B数据，但与旧A boot不同，正式trace仍缺失；旧启动的journal/core未保留。见[分析](results/urma_fadd_fence_observe_01_failure_capture/ANALYSIS_02.zh-CN.md)。

## 运行条件

先与同事协调两端设备可用。最新快照两端均为URMA内核，但URMA设备有他人simulator占用，不能直接运行或结束他人进程。脚本仍执行GPU/设备/barrier/硬件检查，失败即停，不绕过。

## 1. 两端设置

202设RANK=0，204设1。保持前一轮成功的MACA、私有库及额外运行库环境。先拉取最新代码：

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
JOB=urma_fadd_fence_observe_02
DEST="$PWD/test/reference_events/results"
CONSOLE="/home/h00918771/metax_dev/fadd_fence_console/$JOB/node$RANK"
mkdir -p "$CONSOLE"
uname -r
mx-smi
fuser -v /dev/mxcd /dev/uburma/udmac0d1e2 || true
```

预期两端6.6.0-gdr_f00955680_diag，设备无人占用。BUILD复用此前fadd_fence_v1；本轮没有生产C++/CUDA变化，不需重新编译。模块不存在或源码不匹配时才按原构建文档换新BUILD，不能改manifest强行跳过。

## 2. 新启动状态下的128-token门禁

两端执行，202先启动、180秒内204启动。33380/33381空闲：

```bash
bash test/reference_events/run_fadd_fence.sh --part observe-smoke \
  --node-rank "$RANK" --master-addr 141.61.17.202 --port 33380 \
  --build-dir "$BUILD" --job "$JOB" --output "$ROOT" --destination "$DEST" \
  2>&1 | tee "$CONSOLE/observe_smoke.log"
```

此项沿用B的128-token门禁，检查当前模块/boot下采集与正确性；旧启动的smoke不能复用。两端CASE_END rc=0、checks全true、FADD_FENCE_SUITE_PASSED part=observe-smoke才继续。

## 3. 只运行A的三项

```bash
bash test/reference_events/run_fadd_fence.sh --part observe-a \
  --node-rank "$RANK" --master-addr 141.61.17.202 --port 33380 \
  --build-dir "$BUILD" --job "$JOB" --output "$ROOT" --destination "$DEST" \
  2>&1 | tee "$CONSOLE/observe_a.log"
```

顺序为all_on_paced→all_on_trace→all_on_post，只有中间一项开启12点稀疏采集；两端fence始终on，FADD STRONG、WRITE RELAX、窗口8，4096 tokens/hidden7168/experts256/topk8、5预热/10采样/丢首值。原始正确性检查保留。

任何一步失败后停止，不手工跳到后控制或B。脚本会收集已有node/console。保持当前现场并提供日志，避免新任务覆盖、切核导致现场丢失；是否需要后续恢复由错误证据决定。通过后显示FADD_FENCE_SUITE_PASSED part=observe-a，先提供A三项日志评估BF16采集代表性，不立即执行observe或observe-b。

## 4. 提供这些目录

```text
urma_fadd_fence_observe_02_observe_smoke128/
urma_fadd_fence_observe_02_all_on_paced/
urma_fadd_fence_observe_02_all_on_trace/
urma_fadd_fence_observe_02_all_on_post/
```

均保留node0/node1、console/build元数据，尤其normal/preflight_error.txt、bench.log、status、event_trace、reference_validation_error和kernel_before/after（有多少提供多少）。另保留CONSOLE的observe_smoke.log、observe_a.log。不需要.so/core内存，不用重复执行旧JOB的故障收集脚本。

A完成后决定下一步：采集代表性通过才继续B；阶段门禁失败先修对应原因；生产运行失败先分析当前首发错误。该计划的价值是恢复能回答278μs来源的观测路径，不将它当成新的优化收益实验。
