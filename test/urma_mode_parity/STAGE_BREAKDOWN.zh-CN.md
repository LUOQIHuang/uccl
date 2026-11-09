# URMA Normal真实程序：GPU与CPU阶段插桩

本轮专门运行完整Normal的阶段诊断，不运行WRITE微测或LL。沿用原WRITE/FADD顺序、fence、quiet/barrier和completion=all。

RDMA/verbs内核下的对应实验见 [verbs阶段插桩与比较文档](../verbs_baseline/STAGE_BREAKDOWN.zh-CN.md)。当前URMA任务先完成并收集，切到verbs内核后再执行；两套GPU测点一致。

## 1. 能得到哪些数据

| 范围 | 本轮记录 |
| --- | --- |
| 整体调用 | layout、FP8/BF16 Dispatch chunk32、Combine chunk12/16的CUDA-event原始时延 |
| GPU kernel范围 | notify、cached_notify、dispatch、combine的代表warp线程范围 |
| GPU内部 | 24个while位置：发送顺序等待、远端head背压、RDMA元数据/tail、NVL槽/head/tail、接收与协调推进；包含架构分支 |
| GPU算子 | WRITE、atomic、quiet、barrier及命令入队范围 |
| GPU→CPU关联 | 每条命令发布前的GPU clock64、CTA/thread标识，与CPU收集事件按proxy/wr_id关联 |
| Proxy与URMA | collect、prepare、实际SDK post_begin/end、CQE、ack_slot、tail推进；另保留目标offset、payload字节/atomic值 |

GPU内部共33个命名范围，详见随结果保存的 `gpu_stage_sites.json`（含原始位置和while表达式）。循环条件等待与外层推进范围分开记录；NVL与不同架构的分支未执行时可以没有样本。layout目前保留整体API事件时延，不细分其纯计算kernel内部。

每个warp的lane0/1分别采集内部循环；算子和kernel范围只采lane0。每个范围累计次数、周期和最长单次周期，保留ring归属。它是针对当前双节点、每节点单GPU的代表线程诊断，不是全部GPU线程的硬件trace。

**时钟解释必须保留：** GPU周期差在同一执行线程内计算，不相减不同SM的绝对clock64；换算µs使用设备nominal clock，仅为估计，原始cycles保留。CPU阶段使用本机steady_clock。GPU发布tick与CPU收集ns尚未校准，当前只能关联，不能把两者直接相减输出准确的GPU→CPU交接延迟。不同节点时间戳也不相减。

GPU外层推进、内层等待、算子范围存在嵌套；多个warp/Proxy并行。所有sum字段都是累计范围，不可相加成完整Dispatch或算作端到端百分比，也不可将kernel范围减去这些重叠累计值当成纯计算时间。quiet/barrier的GPU等待包含CPU与网络推进依赖，不等于设备本身服务时间。当前FIFO对普通WRITE/atomic在CPU收集后即可pop释放队列空间，ack_slot表示pending命令退休；quiet/barrier的pop则可能等完成。因此普通命令的tail_published可以早于CQE/ack_slot，不把两者顺序强行改成串行链。

## 2. 两份构建、四个双端任务

- 普通构建：编译关闭GPU追踪，三轮完整Normal；每点预热50、采样50，首个丢弃，49个有效。
- 插桩构建：编译启用GPU追踪，单独一个诊断任务；每点预热5、记录5次完整GPU+CPU窗口，首个标记丢弃，另四次供阶段观察。

诊断每个调用前后增加双端同步并转存统计，改变连续排队节奏。GPU atomics、日志及寄存器占用也会扰动执行，因此诊断计时不与普通构建混为性能均值。普通构建与插桩构建源代码/SDK/核心flags一致，只允许GPU追踪编译开关不同。

需要重新构建两份模块。旧 `normal_notify_v1` 不含这些GPU钩子；尚未执行的七任务通知微测套件可留待以后，本轮先只跑本文件四个任务。

## 3. 两端设置环境

202为RANK0，204为RANK1。两端更新代码后，在Bash执行，仅修改RANK：

```bash
cd /home/h00918771/uccl-ep-xpu-integration-new
set -euo pipefail
export URMA_DIAG_HOME=/home/h00918771
export URMA_DIAG_PYTHON=/home/h00918771/metax_dev/venv-maca-py312/bin/python
export MACA_PATH=/opt/maca-3.7.0
export URMA_DIAG_LIBRARY_DIR=/home/h00918771/metax_dev/urma_after_kernel_20261029/diag/umdk_private_618957c3/lib
export URMA_DIAG_INCLUDE_DIR=/home/h00918771/metax_dev/urma_sdk_private_include
RANK=0  # 204改为1
BASE=/home/h00918771/metax_dev/urma_mode_parity_build/stage_base_v1
TRACE=/home/h00918771/metax_dev/urma_mode_parity_build/stage_trace_v1
JOB=urma_stage_01
RESULTS=/home/h00918771/metax_dev/urma_stage_results/$JOB
CONSOLE=/home/h00918771/metax_dev/urma_stage_console/$JOB
mkdir -p "$CONSOLE"
test -s "$URMA_DIAG_INCLUDE_DIR/urma_api.h"
test -d "$URMA_DIAG_LIBRARY_DIR"
uname -r
setpci -s 0000:a0:08.0 CAP_EXP+28.w
mx-smi
```

私有库/SDK目录沿用此前通过的实际位置；保留需要的 `URMA_DIAG_EXTRA_LIBRARY_DIRS`。仍需URMA内核 `6.6.0-gdr_f00955680_diag`、runpm_link=0、P2P bridge、Atomic Request 0x40位及空闲GPU。使用设备udmac0d1e2、GPU0、enp33s0。脚本只检查环境，不修改PCI配置；保留源码默认FIFO backend，不切换队列实现。

## 4. 两端各自构建与检查

```bash
bash test/urma_mode_parity/run.sh --action build --backend urma --mode normal \
  --node-rank "$RANK" --job stage_base_build --build-dir "$BASE" \
  2>&1 | tee "$CONSOLE/build_base_node${RANK}.log"

bash test/urma_mode_parity/run.sh --action build --backend urma --mode normal \
  --node-rank "$RANK" --job stage_trace_build --build-dir "$TRACE" --gpu-stage-build \
  2>&1 | tee "$CONSOLE/build_trace_node${RANK}.log"

bash test/urma_mode_parity/run.sh --action check --backend urma --mode normal \
  --node-rank "$RANK" --job stage_base_check --build-dir "$BASE" --notification-check \
  2>&1 | tee "$CONSOLE/check_base_node${RANK}.log"

bash test/urma_mode_parity/run.sh --action check --backend urma --mode normal \
  --node-rank "$RANK" --job stage_trace_check --build-dir "$TRACE" \
  --phase-suite --api-only --stage-profile --capture-layout --timeline --gpu-stages \
  2>&1 | tee "$CONSOLE/check_trace_node${RANK}.log"
```

两份均应BUILD_PASSED和MODE_PARITY_CHECK_PASSED；普通模块gpu_stage_version=0，插桩模块=1。构建先跑CPU模拟边界测试；开发机另检查过CUDA统计原语，但完整MetaX kernel编译/硬件仍须服务器验证。构建失败即停止，保留两份build.log、host_tests.log和请求manifest，不复用旧模块。

## 5. 双端一键执行

202先启动，180秒内在204启动相同命令，JOB/端口相同：

```bash
bash test/urma_mode_parity/run_stage_breakdown.sh \
  --node-rank "$RANK" --master-addr 141.61.17.202 --job "$JOB" \
  --build-dir "$BASE" --trace-build-dir "$TRACE" \
  --result-root "$RESULTS" --port 32080 \
  2>&1 | tee "$CONSOLE/suite_node${RANK}.log"
```

32080/32081需可用。依次执行baseline_r01/r02/r03和diagnostic。每个任务应CASE_END normal rc=0，两端最后URMA_STAGE_SUITE_ALL_PASSED。保持此前完整Normal设置，不额外改变CPU绑核。失败停止、收集两端当前日志，重跑换新JOB，不自动删除共享资源。

诊断输出每端25行gpu_stages.jsonl及25行urma_timeline.jsonl：五个测点×五个调用。layout允许GPU范围和CPU请求链为空；Dispatch/Combine须有有效请求链和GPU算子/kernel记录。

## 6. 两端分别整理需要的日志

```bash
bash test/urma_mode_parity/run.sh --action collect --collect-suite \
  --node-rank "$RANK" --job "$JOB" --output "$RESULTS"
DEST="$PWD/test/urma_mode_parity/results/$JOB"
mkdir -p "$DEST/console/node${RANK}"
cp -v "$CONSOLE"/*.log "$DEST/console/node${RANK}/"
for KIND in base trace; do
  if [ "$KIND" = base ]; then DIR="$BASE"; else DIR="$TRACE"; fi
  mkdir -p "$DEST/build/node${RANK}/$KIND"
  for NAME in host_tests.log build.log build_request.json manifest.json; do
    if [ -f "$DIR/$NAME" ]; then
      cp -v "$DIR/$NAME" "$DEST/build/node${RANK}/$KIND/"
    fi
  done
done
```

提供 `test/urma_mode_parity/results/$JOB/` 完整目录，两端node0/node1都要有：四任务的setup/host/preflight/runtime/build_manifest、实际Normal源码、api_samples/sample_summary/phases、status/bench.log、内核和计数器前后快照及销毁前测量边界；诊断另保留gpu_stages、urma_timeline、write_layout、gpu_stage_sites；以及console和两份构建日志。无需.so、objects、cache、tmpdir。构建阶段失败没有任务时，直接整理build/console。

## 7. 双端结果齐全后汇总

```bash
"$URMA_DIAG_PYTHON" test/urma_mode_parity/summarize_stage_breakdown.py \
  "test/urma_mode_parity/results/$JOB" \
  --output "test/urma_mode_parity/results/$JOB/summary"
```

成功标志URMA_STAGE_SUMMARY_PASSED。汇总校验双端、三轮普通基线、两份构建开关、相同源码/SDK/内核/boot、25个诊断窗口、有效GPU算子/Dispatch/Combine覆盖、发布关联及CPU时间线。输出normal_runs/normal_aggregate、gpu_stage_scopes、cpu_scopes、request_timeline、timeline_coverage六份CSV。stage站点清单用于解释无样本的架构分支；CPU链按覆盖率评价，不伪造缺失阶段。
