# 当前优先：Normal快速CPU定位（不运行GPU插桩）

只跑两个双端任务：同一base模块的一轮普通计时、一轮轻量CPU统计。两端GPU_STAGE_TRACE=0；无GPU源码计时、无Kineto、无逐请求时间线、无WRITE布局抓取、无微测和参数扫描。保留原正确性检查及五个固定测点。每点预热50/采样50/丢弃首个；重点看FP8/BF16 Dispatch chunk32和Combine chunk16。

## 1. 两端设置

202 RANK0、204 RANK1。先在当前URMA内核做即可；后续有verbs窗口时把BACKEND改为verbs，使用独立BUILD/JOB。不要为了本轮主动切内核。

```bash
cd /home/h00918771/uccl-ep-xpu-integration-new
set -euo pipefail
export URMA_DIAG_HOME=/home/h00918771
export VERBS_DIAG_HOME=/home/h00918771
export URMA_DIAG_PYTHON=/home/h00918771/metax_dev/venv-maca-py312/bin/python
export VERBS_DIAG_PYTHON="$URMA_DIAG_PYTHON"
# 使用这次日志实际采用的工具链；不要测试中途更换。
export MACA_PATH=/opt/maca-3.9.0
export LIBRARY_PATH=/usr/lib/gcc/aarch64-openEuler-linux/12:/usr/lib64${LIBRARY_PATH:+:$LIBRARY_PATH}
export URMA_DIAG_LIBRARY_DIR=/home/h00918771/metax_dev/urma_after_kernel_20261029/diag/umdk_private_618957c3/lib
export URMA_DIAG_INCLUDE_DIR=/home/h00918771/metax_dev/urma_sdk_private_include
BACKEND=urma
RANK=0  # 204改为1
GID=3
PY="$VERBS_DIAG_PYTHON"
BUILD=/home/h00918771/metax_dev/fast_normal_build/${BACKEND}_cpu_01
ROOT=/home/h00918771/metax_dev/fast_normal_results
JOB=${BACKEND}_fast_01
mkdir -p "$ROOT"
uname -r
readlink -f "$MACA_PATH"
df -h / "$ROOT"
mx-smi
```

沿用此前通过的EXTRA_LIBRARY_DIRS、URMA设备/bridge/runpm/PCI Atomic条件或verbs GID配置。保持设备已协调空闲。脚本不修改PCI/驱动/内核、不清理他人进程或kdump文件。Torch仍使用既有2.6.0+metax3.7环境；此轮先验证现有组合，不升级组件。

## 2. 两端分别构建，仅一份base

```bash
bash test/gpu_stage_recovery/run_fast_normal.sh --action build \
  --backend "$BACKEND" --node-rank "$RANK" --job "$JOB" \
  --build-dir "$BUILD" --output "$ROOT" --gid-index "$GID"
```

必须新BUILD，旧v2失败目录的request包含旧源码，不能复用。本次修复MACA3.9 half比较歧义：编译时清除Torch禁用half的宏，显式先包含FP16头文件。不改SDK文件、不关闭FP8。构建先自动执行小编译探针，无GPU执行，随后CPU边界测试和完整扩展编译。成功标志BUILD_PASSED/VERBS_BUILD_PASSED。失败即停止，收集BUILD的precision_probe_*.log、precision_probe_*/request.json和compile_*.log、host_tests.log、build.log；不进入运行。

## 3. 两端执行两个任务

202先启动，180秒内204启动相同命令，JOB/端口相同：

```bash
bash test/gpu_stage_recovery/run_fast_normal.sh --action run \
  --backend "$BACKEND" --node-rank "$RANK" --master-addr 141.61.17.202 \
  --job "$JOB" --build-dir "$BUILD" --output "$ROOT" \
  --gid-index "$GID" --port 32580
```

需要32580/32581可用。先check，再${JOB}_baseline、${JOB}_cpu；每个CASE_END应rc=0，最终FAST_NORMAL_COMMAND_PASSED。任一步失败停止，不进入下一任务。重跑换JOB尾号，不覆盖原日志。CPU统计也有开销，不能将CPU累计范围相加成端到端关键路径。

## 4. 两端收集，失败也收集

```bash
DEST="$PWD/test/gpu_stage_recovery/results/$JOB"
for KIND in baseline cpu; do
  if [ -d "$ROOT/${JOB}_${KIND}/node${RANK}" ]; then
    "$PY" test/gpu_stage_recovery/collect.py "$ROOT/${JOB}_${KIND}" \
      --node-rank "$RANK" --destination "$DEST/${JOB}_${KIND}"
  fi
done
"$PY" test/gpu_stage_recovery/collect.py "$ROOT/$JOB" \
  --node-rank "$RANK" --base-build "$BUILD" --destination "$DEST/$JOB"
```

提供test/gpu_stage_recovery/results/$JOB整个目录，node0/node1都要有。包括基线和CPU任务的原始API采样、sample_summary、phases、status/bench、setup/runtime/manifest/host及内核/网络计数器，另含console和编译探针/build日志；无.so和缓存。此轮不应有GPU stages、GPU profiler trace、write_layout和逐请求timeline。

## 5. 两端结果齐全后汇总

```bash
"$PY" test/gpu_stage_recovery/summarize_fast_normal.py \
  --root "$DEST" --job "$JOB" --output "$DEST/summary"
```

成功标志FAST_NORMAL_SUMMARY_PASSED。api_overhead.csv给出每端每点普通时延、CPU诊断时延和变化百分比；绝对变化超过5%标记perturbed，这是诊断筛选阈值，不是统计显著性证明。cpu_scopes.csv保留每API请求/字节/批次数、CPU线程累计时间和每scope均值；阶段重叠，不可相加。

这一轮用于区分提交侧、完成推进侧的候选瓶颈；如果计数口径不等或诊断显著扰动，不能直接据此归因。只有两套后端相同口径数据齐全后才做性能差值结论；之后只对最有证据的一处实现做单变量A/B。旧的三轮基线可用于稳定性参考，无需为了GPU插桩再次重测。
