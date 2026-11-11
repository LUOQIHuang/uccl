# Dispatch主kernel：URMA / RDMA同测点诊断

**当前请使用 [参考事件式打点运行文档](../reference_events/README.zh-CN.md)。已按940f7c7b参考实现移植Normal Dispatch关键事件、映射主机时钟及CPU请求链，旧RAII方案暂停，以下内容保留为历史实验说明。**

**当前下一步请使用 [CODEGEN.zh-CN.md](CODEGEN.zh-CN.md)。精简到6个范围后，关闭采集的FP8 Dispatch仍慢约54.8%、BF16慢约29.1%，尚未通过代表性门槛。暂停GPU打桩实验，先读取现有objects/模块；无需切内核、独占GPU或重建。以下保留原实验说明及结果格式。**

目标是把已知的Dispatch差额缩小到具体工作或等待范围：发送打包、命令入队、远端空间、接收通知、数据新鲜度、本地转发和消费反馈。两后端复用同一份internode.cu测点；不是再次确认Dispatch总耗时更长。本轮不修改CPU完成轮询实现、不切换强弱序、不运行LL，也不重复WRITE带宽扫描。

**此前v1 GPU打桩发生过系统故障；本入口拒绝v1，使用v2显存私有槽位，并新增Dispatch测点能力检查。开发端没有C500，新增采集尚未经过服务器GPU验证。先逐步做128-token小负载验证，任一步失败即停止，不自动循环跑完。**

## 1. 测什么，如何解读

五类角色各有总范围：Sender、Sender Coordinator、Forwarder、Feedback Coordinator、Receiver。另有以下细分范围，完整ID表见dispatch_sites.json：

| 范围 | 回答的问题 |
| --- | --- |
| 50：token打包及本地发布 | 发送侧数据准备是否增加 |
| 3/4：发送顺序、远端head空间等待 | 是否因发送顺序或远端消费反馈而受阻 |
| 59：整批请求发起；40/41/42：入队、WRITE、atomic算子 | GPU提交请求是否更费时；算子范围包含入队，不是网卡执行时间 |
| 9/11：接收元数据、到达tail等待 | 数据/通知到达推进是否更晚 |
| 12：epoch新鲜度等待 | 收到通知后，是否仍在等本轮数据标记 |
| 13：本地转发空间等待 | 接收后是否被本地消费背压阻塞 |
| 51/52：转发拷贝、输出拷贝及元数据 | 数据到达后的本地处理是否增加 |
| 15/17：本地接收元数据、tail等待 | 最终接收角色是否在等前级 |
| 60：head反馈请求发起 | 消费进度反馈是否受阻；其总协调范围为56 |

每个CTA、warp、采样lane独占统计槽，普通显存读写，无新增GPU统计原子和统计用host映射内存。两个节点采lane0/1，以覆盖各自可能位于lane1的远端atomic；每条记录保留block/warp/lane。开始/结束及统计拷回在CUDA Event计时外，采集后端同步读取。

GPU只在两个Dispatch计时窗口中采集；不为layout、Combine或独立notify kernel记录GPU范围。保留原正确性测试及五个固定API计时点，layout/Combine只是控制，不扫描配置。

算子40/41/42另受主kernel的线程私有标记保护，避免同一次API中的notify算子混入；标记位于独立统计显存，不写入生产命令FIFO。已有CPU测试检查主kernel范围外不记录、范围结束后关闭，以及lane1的远端atomic能被采到。

这些是**有嵌套、并行的线程范围**，不是可以相加的完整关键路径。41包含40，59可能包含41/42，角色总范围包含内部工作/等待。42也包含本地GPU atomic和远端请求入队，需保留lane区分。不能把这些范围相加成总时延，不能将11等同于纯网络传输时间。GPU/CPU时钟未关联，本轮不能单独证明CPU Proxy是根因。

## 2. 两端设置；一次只测当前内核对应的后端

202为RANK=0，204为RANK=1。URMA使用6.6.0-gdr_f00955680_diag，verbs使用6.6.0-145.3.34.165.oe2403sp3.aarch64。脚本校验版本，不切内核、不加载驱动、不修改PCI配置、不结束他人进程。两个后端可在不同时间窗口完成，使用同一提交及同一用户态MACA版本；内核/设备/KMD差异保留在比较上下文中。

```bash
cd /home/h00918771/uccl-ep-xpu-integration-new
set -euo pipefail
export URMA_DIAG_HOME=/home/h00918771
export VERBS_DIAG_HOME=/home/h00918771
export URMA_DIAG_PYTHON=/home/h00918771/metax_dev/venv-maca-py312/bin/python
export VERBS_DIAG_PYTHON="$URMA_DIAG_PYTHON"
export MACA_PATH=/opt/maca-3.7.0
export URMA_DIAG_LIBRARY_DIR=/home/h00918771/metax_dev/urma_after_kernel_20261029/diag/umdk_private_618957c3/lib
export URMA_DIAG_INCLUDE_DIR=/home/h00918771/metax_dev/urma_sdk_private_include
# 保留此前成功使用的EXTRA_LIBRARY_DIRS及LIBRARY_PATH（若有）。
RANK=0  # 204改为1
BACKEND=urma  # RDMA窗口改为verbs，其他命令共用
GID=3
PY="$VERBS_DIAG_PYTHON"
BASE=/home/h00918771/metax_dev/dispatch_diag_build/${BACKEND}_base_v1
TRACE=/home/h00918771/metax_dev/dispatch_diag_build/${BACKEND}_trace_v1
ROOT=/home/h00918771/metax_dev/dispatch_diag_results
DEST="$PWD/test/dispatch_diag/results"
mkdir -p "$ROOT" "$DEST"
uname -r
mx-smi
df -h / "$ROOT"
```

SDK路径以此前验证通过的真实路径为准。MACA3.7是历史URMA配方；若当前只能使用其他版本，先固定两端同一可用版本，另一后端也使用该用户态版本，否则汇总保留单端结果、拒绝直接跨后端比较。不能把MACA3.9下已异常回退的verbs数据混入历史3.7基线。

## 3. 两端分别构建base、trace，各一次

新增测点要求新目录，旧v1/v2模块没有get_dispatch_trace_version=1，入口会拒绝。共用源码只相差UCCL_GPU_STAGE_TRACE编译开关；每份构建用1个编译任务，先precision probe和CPU模拟，再编译扩展。不自动重试。

```bash
bash test/dispatch_diag/run.sh --action build --backend "$BACKEND" \
  --step baseline --node-rank "$RANK" --job "${BACKEND}_dispatch_build_base" \
  --build-dir "$BASE" --output "$ROOT" --gid-index "$GID"

bash test/dispatch_diag/run.sh --action build --backend "$BACKEND" \
  --step trace --node-rank "$RANK" --job "${BACKEND}_dispatch_build_trace" \
  --build-dir "$TRACE" --output "$ROOT" --gid-index "$GID"
```

成功标志BUILD_PASSED或VERBS_BUILD_PASSED。失败保留console、build.log、host_tests.log、request/build_request、precision_probe日志，停止后续；之前完整构建曾中断，不通过反复重建尝试恢复。collector可收集这些诊断文件，无需.so、objects。

## 4. 首次GPU验证：128 tokens，逐步运行

两端同一STEP、JOB、端口，202先启动，180秒内启动204。每步预热1、采样3，丢弃首个。步骤间确认两端成功并收集日志，再手动修改STEP；不写循环自动执行。

| STEP顺序 | 检查内容 |
| --- | --- |
| bypass | trace编译，GPU统计指针为空，不写统计、不读clock64 |
| access | 仅Dispatch槽位访问和calls计数，不读时钟 |
| clock | 只对Dispatch整体线程范围读取时钟 |
| trace | 五类角色、拷贝和等待范围全部采集 |

```bash
STEP=bypass  # 成功后依次手动改为access、clock、trace
JOB="${BACKEND}_dispatch_${STEP}_t128_01"
bash test/dispatch_diag/run.sh --action run --backend "$BACKEND" \
  --step "$STEP" --node-rank "$RANK" --master-addr 141.61.17.202 \
  --job "$JOB" --build-dir "$TRACE" --tokens 128 --output "$ROOT" \
  --gid-index "$GID" --port 32880

"$PY" test/gpu_stage_recovery/collect.py "$ROOT/$JOB" \
  --node-rank "$RANK" --base-build "$BASE" --trace-build "$TRACE" \
  --destination "$DEST/$JOB"
```

每条run命令先本地check，再启动双端benchmark。每端应CASE_END normal rc=0，checks中的dispatch_coverage=True，最后DISPATCH_DIAG_COMMAND_PASSED。只产生layout或缺角色、缺任一Dispatch窗口、重复槽位、旧schema都会失败；不能只看文件存在。bypass记录应为空，access cycles应全0，clock只有46，trace要求五角色、拷贝和算子覆盖；条件性epoch/head反馈等范围可没有调用，coverage会列出。

GPU统计文件为每端6个窗口（FP8/BF16各3）。任何失败停止并收集；若系统故障，不继续其他步骤，保留内核现场和已有文件。新增采集没有硬件验证，这一步不预设为必然成功。

## 5. 正式4096-token定位：baseline → trace → post

小负载四步通过后，正式只做下列三个任务，每个都是两端同步的新进程。端口仍为32880/32881，每次只运行一项：

```bash
bash test/dispatch_diag/run.sh --action run --backend "$BACKEND" \
  --step baseline --node-rank "$RANK" --job "${BACKEND}_dispatch_base_01" \
  --build-dir "$BASE" --tokens 4096 --output "$ROOT" --gid-index "$GID"
```

两端成功后运行trace：

```bash
bash test/dispatch_diag/run.sh --action run --backend "$BACKEND" \
  --step trace --node-rank "$RANK" --job "${BACKEND}_dispatch_trace_01" \
  --build-dir "$TRACE" --tokens 4096 --output "$ROOT" --gid-index "$GID"
```

两端成功后回测未插桩base：

```bash
bash test/dispatch_diag/run.sh --action run --backend "$BACKEND" \
  --step post --node-rank "$RANK" --job "${BACKEND}_dispatch_post_01" \
  --build-dir "$BASE" --tokens 4096 --output "$ROOT" --gid-index "$GID"
```

baseline/post预热50、计时50，49个有效样本；trace预热5、计时10，9个有效GPU窗口/测点。两端trace各20个GPU窗口，其他API不采GPU。采集会增加分支、时钟和显存访问，影响寄存器/调度；用前后基线检查漂移和整体扰动，不能直接把插桩API时延当作正式性能基线。

若128成功而4096失败，同样停止，不跳过覆盖检查。重跑换JOB尾号，并相应修改后面的目录参数，不覆盖已有目录。

## 6. 收集哪些数据

对每个完成或失败的正式JOB分别执行（下面先收集base，再把JOB改成trace、post各执行一次）：

```bash
JOB="${BACKEND}_dispatch_base_01"
"$PY" test/gpu_stage_recovery/collect.py "$ROOT/$JOB" \
  --node-rank "$RANK" --base-build "$BASE" --trace-build "$TRACE" \
  --destination "$DEST/$JOB"
```

提供test/dispatch_diag/results下本轮两个后端的目录：三项正式任务各自node0/node1和console；首次小负载bypass/access/clock/trace也保留。必须包含setup、host、runtime、模块manifest、冻结benchmark、API原始samples/summary、gpu_stages、dispatch_coverage、dispatch_sites、status、bench、GPU/内核快照及build诊断；collector排除二进制和cache。URMA失败时另有dispatch_validation_error.txt，verbs失败原因在status.error或console。

## 7. 每个后端汇总，以及跨后端比较

两端齐全后先按后端汇总：

```bash
"$PY" test/dispatch_diag/summarize.py \
  --baseline "$DEST/${BACKEND}_dispatch_base_01" \
  --trace "$DEST/${BACKEND}_dispatch_trace_01" \
  --post "$DEST/${BACKEND}_dispatch_post_01" \
  --output "$DEST/${BACKEND}_summary_01"
```

成功标志DISPATCH_DIAG_SUMMARY_PASSED，输出：

- api_controls.csv：两个Dispatch的base/trace/post时延、CV、基线漂移和诊断扰动。
- scopes_by_thread.csv：按节点、FP8/BF16、block/warp/lane、测点的累计时间分布和调用数。条件性范围未调用的窗口按0次记录，避免只挑发生等待的样本。
- scope_maxima.csv：每窗口在各线程中取最大的范围耗时，再对9个窗口取中位数及范围；用来筛查等待集中在哪类角色，不能当成端到端时延分量。
- context.json：源代码、实际MACA库哈希、GPU/KMD、内核和测点定义。

微秒为clock64按nominal_clock_khz换算的估计，原始cycles必须保留。两个后端摘要都有后：

```bash
"$PY" test/dispatch_diag/summarize.py \
  --verbs-summary "$DEST/verbs_summary_01" --urma-summary "$DEST/urma_summary_01" \
  --output "$DEST/comparison_01"
```

成功标志DISPATCH_DIAG_COMPARISON_PASSED。不同测点、源码、负载、用户态MACA会拒绝直接比较；不同内核/设备/KMD记录为环境差异，不伪装成纯后端代码A/B。若基线漂移或诊断时延偏离前后基线参考值超过5%，仍保留本后端摘要，但拒绝跨后端归因比较。5%只是代表性筛选阈值，不是显著性检验；诊断还改变了逐调用同步节奏，因此不能把差值全部称为计时指令成本。

跨后端输出api_comparison.csv（前后未插桩控制的均值参考）和scope_comparison.csv（各范围最大线程时间的对比），并保存两套环境上下文。它们分别对应整体差额和内部范围候选，不能把后者加总来解释前者。

这轮应输出的结论形式是“URMA增加的时间集中在远端空间等待/到达tail等待/epoch等待/入队/拷贝中的哪些范围”。若差异不清晰或扰动过大，应报告测量限制，不能继续将Proxy慢、GPU计算慢或网络慢作为既定结论。
