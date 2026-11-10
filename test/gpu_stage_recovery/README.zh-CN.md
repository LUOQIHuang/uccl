# GPU阶段插桩v2：故障修复与分步运行

本文件替代旧STAGE_BREAKDOWN中的直接全量诊断流程。旧stage_trace_v1曾导致节点失联/重启，不再运行；新驱动要求gpu_stage_version=2，拒绝v1模块。新方案已做本地CPU模拟和CUDA编译检查，尚未在C500上验证，不能保证已消除系统故障。

## 1. 本次修改及边界

- 删除FIFO/ring中的GPU统计状态和发布tick记录，恢复生产队列数据结构；原有write/atomic、fence、quiet/barrier和pop/完成语义保留。
- GPU统计使用cudaMalloc显存；(CTA,warp,采样lane,stage)独占槽位，普通读写累计，无atomicAdd/atomicMax，无统计用host映射内存。CPU在GPU同步后一次性拷回。
- 关闭采集时设备侧控制指针为空，在访问统计状态前返回，不再通过FIFO句柄获取状态。bypass窗口也不分配统计缓冲区、不读取clock64。
- 一次采集约12MiB显存；最多128个CTA、每CTA最多32个warp，内部循环采lane0/1，kernel/算子采lane0。超界或同窗口多Normal stream直接报错，不并发覆盖槽位。一个进程只允许一个窗口、一个GPU。
- 33个范围仍是包含等待的代表线程统计；嵌套/并行范围不可相加。周期换算用nominal clock，为估计。v2不再提供GPU发布tick与CPU collect的关联，CPU请求链保留；ring=0为兼容占位，归属改看block/warp/sample_lane。不是完整可相加关键路径。

报告中的KMD根因判断尚缺panic栈。此版是移除可疑插桩机制的修复，不宣称已经证明某条GPU指令或厂商驱动是根因。

## 2. 本轮只在当前可用内核测试

两端先更新到同一提交。当前若为URMA内核，BACKEND=urma；若为verbs内核，改为verbs。不要为了短验证反复切内核。202为RANK0，204为RANK1。

```bash
cd /home/h00918771/uccl-ep-xpu-integration-new
set -euo pipefail
export URMA_DIAG_HOME=/home/h00918771
export VERBS_DIAG_HOME=/home/h00918771
export URMA_DIAG_PYTHON=/home/h00918771/metax_dev/venv-maca-py312/bin/python
export VERBS_DIAG_PYTHON="$URMA_DIAG_PYTHON"
export MACA_PATH=/opt/maca-3.7.0
# 报告中已需要的链接路径；保留系统其他已有项。
export LIBRARY_PATH=/usr/lib/gcc/aarch64-openEuler-linux/12:/usr/lib64${LIBRARY_PATH:+:$LIBRARY_PATH}
export URMA_DIAG_LIBRARY_DIR=/home/h00918771/metax_dev/urma_after_kernel_20261029/diag/umdk_private_618957c3/lib
export URMA_DIAG_INCLUDE_DIR=/home/h00918771/metax_dev/urma_sdk_private_include
RANK=0  # 204改为1
BACKEND=urma
GID=3
PY="$VERBS_DIAG_PYTHON"
BASE=/home/h00918771/metax_dev/stage_recovery_build/${BACKEND}_base_v2
TRACE=/home/h00918771/metax_dev/stage_recovery_build/${BACKEND}_trace_v2
ROOT=/home/h00918771/metax_dev/stage_recovery_results
mkdir -p "$ROOT"
uname -r
df -h / "$ROOT"
readlink -f "$MACA_PATH"
mx-smi
fuser /dev/mxcd || true
```

按之前通过的配置保留EXTRA_LIBRARY_DIRS。确认设备已协调空闲、磁盘有空间；URMA还需既有bridge/runpm/Atomic Request检查通过，verbs需rocep40s0f0的可互通RoCE v2 GID。脚本不kill他人进程、不改PCI、不切内核、不删除vmcore、不停止kdump。已有panic证据优先保存vmcore-dmesg或panic栈；不要为取日志主动重复旧故障。

## 3. 两端分别构建两份新模块

必须新目录，不能用旧stage_base/trace_v1。代码更新后也不能复用旧manifest绕过哈希检查。

```bash
bash test/gpu_stage_recovery/run.sh --action build --backend "$BACKEND" \
  --step cpu --node-rank "$RANK" --job "${BACKEND}_v2_build_base" \
  --build-dir "$BASE" --output "$ROOT" --gid-index "$GID"

bash test/gpu_stage_recovery/run.sh --action build --backend "$BACKEND" \
  --step bypass --node-rank "$RANK" --job "${BACKEND}_v2_build_trace" \
  --build-dir "$TRACE" --output "$ROOT" --gid-index "$GID"
```

成功应BUILD_PASSED/VERBS_BUILD_PASSED。base关闭编译插桩，trace开启v2，后续各组共用trace，无需逐组编译。构建先跑CPU边界测试，包括生产FIFO、统计私有槽位及生产GPU运行时桥接逻辑；模拟不证明真实MetaX ABI或硬件稳定性。失败停止，直接收集本节build日志，不启动GPU用例。

## 4. 第一轮先只测cpu和bypass

每一步两端使用相同STEP/TOKENS/JOB/端口，202先启动、204在180秒内启动。每步是新进程，保留完整正确性检查及五个Normal测点；预热1、采样3，首个丢弃。默认128 tokens，hidden7168、experts256、topk8；这验证功能和采集，不做稳定性能结论。

依次执行以下表格。**不要写循环自动跑完；两端确认当前步成功后再改STEP。**

| STEP | BUILD | 验证内容 |
| --- | --- | --- |
| cpu | BASE | 不含GPU插桩，保留CPU阶段/请求时间线 |
| bypass | TRACE | trace编译，设备记录指针为空，不访问记录、不读时钟 |
| access | TRACE | 显存私有槽位calls累计，不读时钟 |
| clock | TRACE | 只计dispatch kernel范围的clock64差值 |
| kernels | TRACE | 四个kernel范围 |
| waits | TRACE | 24个内部等待/推进范围 |
| operators | TRACE | 入队、WRITE/atomic/quiet/barrier范围 |
| all | TRACE | 全部33个范围 |

先设置STEP=cpu运行下面的命令，再设置STEP=bypass重复。**第一次反馈至少提供这两步的结果；若任一步失败，不进入access。** 每步脚本仅运行该步，不会自动进入下一步。

```bash
STEP=cpu
TOKENS=128
JOB="${BACKEND}_recover_${STEP}_t${TOKENS}_01"
if [ "$STEP" = cpu ]; then BUILD="$BASE"; else BUILD="$TRACE"; fi

bash test/gpu_stage_recovery/run.sh --action check --backend "$BACKEND" \
  --step "$STEP" --node-rank "$RANK" --job "$JOB" --build-dir "$BUILD" \
  --tokens "$TOKENS" --output "$ROOT" --gid-index "$GID"

bash test/gpu_stage_recovery/run.sh --action run --backend "$BACKEND" \
  --step "$STEP" --node-rank "$RANK" --master-addr 141.61.17.202 \
  --job "$JOB" --build-dir "$BUILD" --tokens "$TOKENS" \
  --output "$ROOT" --gid-index "$GID" --port 32480

"$PY" test/gpu_stage_recovery/validate.py "$ROOT/$JOB" --node-rank "$RANK"
```

成功要求check通过、CASE_END rc=0，并有GPU_STAGE_RECOVERY_NODE_VALIDATED。命令最后COMMAND_PASSED只代表该条命令成功，不替代数据校验。gpu_stage_version应base=0、trace=2。trace每步应15行GPU统计与15行CPU时间线；layout可以为空。bypass全部GPU records为空；access有calls但cycles为0；其他组要求对应覆盖。

若MULTIPLE_STREAMS或LAUNCH_BOUNDS，记录校验限制已拒绝不受支持用例，保留日志，不绕过保护。若出现系统故障，停止全部后续组，保留已有终端/内核信息。重跑使用新JOB尾号，不覆盖原结果。

cpu/bypass通过并收集后，可继续依次access、clock、kernels、waits、operators、all。128-token全部通过后，将TOKENS=4096，先bypass，再按相同顺序验证至all，仍逐步确认。小负载成功不能代替4096-token验证。每端可随时收集，不必等所有步骤完成。

## 5. 每一步收集；失败也收集

保持当前步骤的JOB变量，在两端分别执行：

```bash
"$PY" test/gpu_stage_recovery/collect.py "$ROOT/$JOB" \
  --node-rank "$RANK" --base-build "$BASE" --trace-build "$TRACE"
```

提供test/gpu_stage_recovery/results/$JOB完整目录，两端node0/node1和console都要有。collector会保留setup、runtime、manifest、实际benchmark、status、bench、api_samples/sample_summary、phases、timeline、GPU记录、内核/计数器快照、console及两份build日志；不带.so、objects、cache。没有status本身就是失败证据，不补造状态。构建失败时也可用其build任务的JOB收集console/build。

两端日志汇合后可再次验证：

```bash
"$PY" test/gpu_stage_recovery/validate.py "test/gpu_stage_recovery/results/$JOB"
```

成功标志GPU_STAGE_RECOVERY_VALIDATED。

## 6. 何时恢复性能对比

先不重复已有三轮基线。只有4096-token all在两端完成，才恢复完整性能基线与诊断对照。届时需要同一v2源码的base/trace、相同负载；旧基线作为历史参考，不与v2阶段值直接混成一组。全量套件可继续使用两份STAGE_BREAKDOWN中的三轮基线+一轮诊断，但优先先提供本文件恢复结果。

v2的block/warp私有槽位与v1的host共享累计实现不同，跨版本不能直接比较阶段数值；整体API趋势也应标注构建版本。移除GPU发布记录后，没有精确GPU→CPU交接延迟；CPU时间线与GPU范围仍需分开解释。
