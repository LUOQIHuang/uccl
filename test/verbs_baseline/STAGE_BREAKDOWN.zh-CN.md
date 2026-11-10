# RDMA/verbs Normal：与URMA对应的GPU与CPU阶段插桩

**当前只定位Dispatch时，使用[Dispatch专用打桩文档](../dispatch_diag/README.zh-CN.md)。它与URMA共用测点，单独验收FP8/BF16角色覆盖，不要求启用CPU逐请求时间线和布局采集。**

**更新：旧v1插桩曾导致系统故障，请先按 [GPU插桩v2分步恢复文档](../gpu_stage_recovery/README.zh-CN.md) 验证，不直接运行下列全量诊断。v2不再记录GPU发布tick；旧文有关该项描述仅适用于v1。**


本轮在verbs内核下运行，不启动URMA，不运行LL或微测。当前正在运行的URMA阶段测试先正常完成、收集日志，协调切换到verbs内核后再执行本文。无需为本次verbs脚本中断URMA测试。

使用当前源码重新构建的纯verbs模块（USE_URMA=0、DMA-BUF），不使用历史ep_host_atomic私有二进制。它对照的是当前URMA源码的verbs分支，不是声称复现历史原版二进制的精确构建。

## 1. 两条路径的共同测量口径

| 项目 | 本轮verbs与URMA阶段测试 |
| --- | --- |
| 节点/GPU | 202 rank0，204 rank1；每节点一张MetaX C500、GPU0 |
| Normal负载 | tokens4096、hidden7168、experts256、topk8、topk-groups2 |
| 五个计时点 | layout、FP8 Dispatch chunk32、BF16 Dispatch chunk32、Combine chunk12/16 |
| GPU测点 | 复用相同33个范围：24个循环、WRITE/atomic/quiet/barrier、入队及四个kernel范围 |
| GPU统计 | 每warp lane0/1采内部循环；算子和kernel采lane0；calls/sum_cycles/max_cycles |
| CPU请求链 | collect → prepare → post_begin/end → CQE → ack_slot；另记录队列tail释放 |
| 发布关联 | GPU发布clock64和CTA/thread，通过proxy/wr_id与CPU收集对应 |
| 基线 | 普通构建三次独立进程，每点预热50、测50、丢弃首个 |
| 诊断 | 插桩构建一次独立进程，每点预热5、测5；五点共25个GPU/CPU窗口，首个标记丢弃 |

沿用默认FIFO，不切换队列实现。普通WRITE/atomic的FIFO空间可在CPU收集后释放；ack_slot表示pending命令完成退休；quiet/barrier的pop可能等待完成。因此tail释放不保证晚于CQE。保留现有fence和完成语义，不为对照改变算子。

verbs使用ibv_post_send/ibv_poll_cq，保留payload_bytes、remote_address、atomic_value；URMA保留remote_offset。远端绝对地址与offset不可直接按数值比较。各后端opcode编号也不等价，不能直接按编号匹配操作。

GPU范围是包含内部等待的代表线程统计，外层与内层有重叠，多个warp/Proxy并行；不可相加成整体时延或百分比。cycles换算µs采用nominal clock，仅为估计。GPU/CPU绝对时钟尚未校准，发布tick只能用于关联；不相减两个节点的时间戳。layout保留整个API时延，未细分纯计算kernel。

诊断的GPU统计原子操作、逐调用双端同步和CPU日志会扰动执行，诊断时延不进入普通基线。请求链CQE耗时包含轮询观察延迟，不等于纯网络时延。

## 2. 两端环境与GID确认

切到可运行verbs的内核后，两端在Bash执行，仅RANK不同：

```bash
cd /home/h00918771/uccl-ep-xpu-integration-new
set -euo pipefail
export VERBS_DIAG_HOME=/home/h00918771
export VERBS_DIAG_PYTHON=/home/h00918771/metax_dev/venv-maca-py312/bin/python
export MACA_PATH=/opt/maca-3.7.0
RANK=0  # 204改为1
GID=3
BASE=/home/h00918771/metax_dev/verbs_baseline_build/stage_base_v1
TRACE=/home/h00918771/metax_dev/verbs_baseline_build/stage_trace_v1
JOB=verbs_stage_01
RESULTS=/home/h00918771/metax_dev/verbs_stage_results/$JOB
CONSOLE=/home/h00918771/metax_dev/verbs_stage_console/$JOB
mkdir -p "$CONSOLE"
uname -r
mx-smi
ibv_devinfo -d rocep40s0f0
cat /sys/class/infiniband/rocep40s0f0/ports/1/gids/$GID
cat /sys/class/infiniband/rocep40s0f0/ports/1/gid_attrs/types/$GID
```

GID3沿用上一轮通过的verbs测试，不将最初环境表中的GID1强加到当前机器。若实际可互通的RoCE v2项为1，修改GID变量即可；两端确认非零、类型v2和网络可互通。默认HCA rocep40s0f0、port1、控制接口enp33s0；MTU使用生产QP的实际active配置并保存rdma_port日志。脚本不修改网络或PCI配置。

保留之前通过的VERBS_DIAG_EXTRA_LIBRARY_DIRS（若有），使用MetaX虚拟环境Python，不使用系统Python。当前源码构建的扩展若加载liburma，会在预检拒绝；不要求卸载系统URMA包。GPU保持空闲，不额外设置绑核或调优变量。

## 3. 两端构建普通版和插桩版

必须使用新BASE/TRACE目录，旧notify_v1模块不能替代。两端分别执行，无需同时开始：

```bash
bash test/verbs_baseline/run.sh --action build --variant current \
  --mode normal --node-rank "$RANK" --job stage_base_build \
  --build-dir "$BASE" --gid-index "$GID" \
  2>&1 | tee "$CONSOLE/build_base_node${RANK}.log"

bash test/verbs_baseline/run.sh --action build --variant current \
  --mode normal --node-rank "$RANK" --job stage_trace_build \
  --build-dir "$TRACE" --gid-index "$GID" --gpu-stage-build \
  2>&1 | tee "$CONSOLE/build_trace_node${RANK}.log"

bash test/verbs_baseline/run.sh --action check --variant current \
  --mode normal --node-rank "$RANK" --job stage_base_check \
  --build-dir "$BASE" --gid-index "$GID" --notify-check \
  2>&1 | tee "$CONSOLE/check_base_node${RANK}.log"

bash test/verbs_baseline/run.sh --action check --variant current \
  --mode normal --node-rank "$RANK" --job stage_trace_check \
  --build-dir "$TRACE" --gid-index "$GID" --notify-check \
  --no-profile --repeats 1 --stage-suite --cpu-diagnostics --timeline --gpu-stages \
  2>&1 | tee "$CONSOLE/check_trace_node${RANK}.log"
```

成功标志VERBS_BUILD_PASSED和VERBS_CHECK_PASSED；普通版gpu_stage_version=0，插桩版=1，queue_backend=fifo。构建先跑真实verbs头文件下的模拟provider测试、GPU统计原语和FIFO base/trace CPU边界测试。完整MetaX编译与硬件行为仍需服务器验证。

若失败即停止，保留两份build.log、host_tests.log、request.json、manifest.json（存在的文件即可）及console日志，不修改manifest绕过检查。

## 4. 四个双端任务

202先启动，180秒内在204启动相同命令；JOB、GID、端口一致，RANK不同：

```bash
bash test/verbs_baseline/run_stage_breakdown.sh \
  --node-rank "$RANK" --master-addr 141.61.17.202 --job "$JOB" \
  --build-dir "$BASE" --trace-build-dir "$TRACE" --gid-index "$GID" \
  --result-root "$RESULTS" --port 32280 \
  2>&1 | tee "$CONSOLE/suite_node${RANK}.log"
```

32280/32281需可用。依次执行baseline_r01/r02/r03和diagnostic，保留原Normal正确性检查。基线关闭Kineto和GPU/CPU阶段统计；诊断也不启用Kineto，直接读取源码插桩。每任务应VERBS_CASE_END ... rc=0，两端最终VERBS_STAGE_SUITE_ALL_PASSED。失败停止，重跑更换新JOB。

每个基线case在node*/r01_baseline/normal下；诊断在node*/cpu_diagnostic/normal下，包含25行gpu_stages.jsonl、25行verbs_timeline.jsonl、五个CPU汇总窗口和write_layout。layout可以没有网络请求及GPU范围；Dispatch/Combine必须有有效范围和请求链。测量结束、benchmark销毁前另保存内核日志边界。

## 5. 收集需要提供的日志

两端分别执行：

```bash
DEST="$PWD/test/verbs_baseline/results/$JOB"
"$VERBS_DIAG_PYTHON" test/verbs_baseline/collect_campaign.py \
  --root "$RESULTS" --job "$JOB" --node-rank "$RANK" --destination "$DEST"
mkdir -p "$DEST/console/node${RANK}"
cp -v "$CONSOLE"/*.log "$DEST/console/node${RANK}/"
for KIND in base trace; do
  if [ "$KIND" = base ]; then DIR="$BASE"; else DIR="$TRACE"; fi
  mkdir -p "$DEST/build/node${RANK}/$KIND"
  for NAME in host_tests.log build.log request.json manifest.json; do
    if [ -f "$DIR/$NAME" ]; then
      cp -v "$DIR/$NAME" "$DEST/build/node${RANK}/$KIND/"
    fi
  done
done
```

提供test/verbs_baseline/results/$JOB完整目录：四任务的node0/node1，包括setup、host、module_manifest、runtime/import、benchmark源码、rdma_port、gpu_stage_sites；每case的bench/status/config、api_samples/sample_summary、内核和计数器前后日志及measurement_boundary；诊断另有gpu_stages、verbs_phases、verbs_timeline和write_layout。另保留console和两份构建日志。无需.so、objects、cache、tmpdir。若构建失败尚无任务，只提供build/console。

## 6. 单后端汇总与跨后端比较

两端结果齐全后：

```bash
"$VERBS_DIAG_PYTHON" test/verbs_baseline/summarize_stage_breakdown.py \
  "test/verbs_baseline/results/$JOB" \
  --output "test/verbs_baseline/results/$JOB/summary"
```

成功标志VERBS_STAGE_SUMMARY_PASSED。输出六份CSV：normal_runs、normal_aggregate、gpu_stage_scopes、cpu_scopes、request_timeline、timeline_coverage。整体基线使用三次独立进程均值的中位数，同时保留最小/最大轮次；GPU和CPU诊断不混入基线。

URMA阶段结果也齐全后，修改下列URMA_JOB为真实任务名，运行：

```bash
URMA_JOB=urma_stage_01
"$VERBS_DIAG_PYTHON" test/verbs_baseline/compare_stage_breakdown.py \
  --verbs "test/verbs_baseline/results/$JOB" \
  --urma "test/urma_mode_parity/results/$URMA_JOB" \
  --output "test/verbs_baseline/results/$JOB/comparison"
```

工具先独立校验两套四任务、两端结果，再比较主机、GPU编号、Torch、控制接口、prepared Normal程序、共同编译源码、核心flags、FIFO及测点版本。最好两套实验使用同一提交；已经按8d0a6b88运行的URMA结果可以继续使用：仅允许已知的verbs_stage_diag.hpp新增诊断字段这一精确差异，其他共享编译源码差异会拒绝。

成功标志STAGE_COMPARISON_PASSED，输出：

- normal_comparison.csv：URMA/verbs整体µs、差值、比值、劣化百分比及三轮范围。
- gpu_scope_comparison.csv：丢弃index0后、按calls加权的代表线程范围均值及最长值；缺失分支留空。
- request_latency_comparison.csv：丢弃index0后的本机请求阶段median/p95与请求数；混合WRITE/atomic分布，仅用于诊断，不能当作纯硬件操作时延。
- cpu_scope_comparison.csv：每个API调用的CPU线程累计范围（五次窗口总量除以5，包含index0），不可相加成端到端时延。
- comparison_context.json：两套内核、boot、库和已知源码差异。

不同后端需要不同内核，比较包含kernel/driver/provider环境影响，不能据此单独归因某个算子。旧URMA结果未保存Buffer/utils源码副本，工具只验证已保存的Normal程序及编译源码，不能宣称所有Python来源已完整验证；这一限制也写入context。GPU/CPU时钟与重叠范围限制同第1节，阶段范围比较用于找等待位置，不生成伪造的可相加关键路径。
