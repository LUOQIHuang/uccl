# 参考版本移植：Normal Dispatch 事件打点

URMA匹配paced/on已全部通过，四个Dispatch采集影响仅+0.28%～+0.90%，见[最新阶段分析](results/MATCHED_TRACE_ANALYSIS.zh-CN.md)。URMA侧开关验收收束，不重复既有实验；下一步只在verbs可用窗口按[同口径文档](VERBS_MATCHED.zh-CN.md)补另一后端记录。原连续基线/on核查见[4096报告](results/FORMAL4096_ANALYSIS.zh-CN.md)，两种采样方式分别保留。

本次smoke128若出现`urma_bind_jetty rc=4096`，先按[建连失败排查流程](RECOVERY.zh-CN.md)处理；保留现有模块，先做一次关闭所有新打桩的128-token对照，不直接重跑4096或重新编译。

## 1. 本轮内容

参考提交 `940f7c7b`，目录 `reference/f00955680_ll28_maca39_build_a3/tree/`。复用其独立事件、设备显存计数/记录、GPU读取周期性更新主机时钟、epoch分窗的设计。提供的test.py/test_eager.py是LL正确性入口，未驱动trace接口；本入口补上Normal控制、采集和解析，使用当前通信实现。

本轮仅采Normal Dispatch。URMA与verbs使用相同GPU测点，CPU保留poll、decoded、prepare、post_begin/end、cqe、ack_slot等请求事件。CPU/GPU采样主机时钟均为CLOCK_MONOTONIC_RAW。GPU记录保留clock64读时钟前后括号、epoch、block，并增加warp标识。记录、计数原子位于设备显存；映射主机页只用于GPU读取时钟。

| 事件 | 精确位置及含义 |
| --- | --- |
| 0 | Sender首个token进入打包前；已经通过该token的远端空间检查，不是kernel入口 |
| 1 | Sender Coordinator首个远端payload WRITE算子调用前，不是网卡开始传输 |
| 2 | Forwarder观察到该通道最后一个远端token的epoch标记；尚不代表全部转发/输出完成 |

不使用此前跨角色RAII计时对象，旧GPU trace编译开关为0。也不复制参考树中的LL步长、流控等其他改动。CPU采用参考实现的线程本地预留记录方式，复用现有请求链挂点，避免逐事件争用全局记录锁；结束窗口后合并，超过总上限32768则记录dropped并判定失败。在verbs补充不启用统计计数器的事件路径，不添加额外网络marker消息。Normal的layout、Combine12/16仍计API时间，作为控制项，不采GPU/CPU事件。本轮不跑LL。

**本地没有C500，不能把参考原版的运行通过等同于此次移植已在服务器验证。**先128-token采集验证，再做4096-token普通基线→事件采集→普通回测。无需再做旧版四种采集模式或先跑roles-only实验。

## 2. 两端设置

202设RANK=0，204设1；一次只用当前内核对应后端。URMA要求6.6.0-gdr_f00955680_diag，verbs要求6.6.0-145.3.34.165.oe2403sp3.aarch64。入口不切内核或改驱动。

```bash
cd /home/h00918771/uccl-ep-xpu-integration-new
set -euo pipefail
export URMA_DIAG_HOME=/home/h00918771
export VERBS_DIAG_HOME="$URMA_DIAG_HOME"
export URMA_DIAG_PYTHON=/home/h00918771/metax_dev/venv-maca-py312/bin/python
export VERBS_DIAG_PYTHON="$URMA_DIAG_PYTHON"
export MACA_PATH=/opt/maca-3.9.0
export URMA_DIAG_LIBRARY_DIR=/home/h00918771/metax_dev/urma_after_kernel_20261029/diag/umdk_private_618957c3/lib
export URMA_DIAG_INCLUDE_DIR=/home/h00918771/metax_dev/urma_sdk_private_include
# SDK include与EXTRA_LIBRARY_DIRS/LIBRARY_PATH沿用实际验证成功的路径。
RANK=0  # 204改1
BACKEND=urma  # verbs窗口改为verbs，其余命令共用；不要为本轮主动切内核。
GID=3
PY="$VERBS_DIAG_PYTHON"
BASE=/home/h00918771/metax_dev/reference_event_build/${BACKEND}_base_v1
TRACE=/home/h00918771/metax_dev/reference_event_build/${BACKEND}_events_v1
ROOT=/home/h00918771/metax_dev/reference_event_results
DEST="$PWD/test/reference_events/results"
mkdir -p "$ROOT" "$DEST"
uname -r
mx-smi
```

两端同一提交、同一MACA/SDK和相同JOB。新测点必须新构建，不能用之前roles/full trace模块。正式参数为4096 tokens、hidden7168、experts256、topk8、每节点GPU0；Dispatch固定chunk32，沿用当前Normal benchmark的SM24/NVL chunk4。URMA默认Normal WRITE relax、completion all、FIFO不变。verbs沿用DMA-BUF、rocep40s0f0、GID3。

## 3. 两端各构建两份模块，各一次

```bash
bash test/reference_events/run.sh --action build --backend "$BACKEND" \
  --step baseline --node-rank "$RANK" --job "${BACKEND}_ref_build_base_01" \
  --build-dir "$BASE" --output "$ROOT" --gid-index "$GID"

bash test/reference_events/run.sh --action build --backend "$BACKEND" \
  --step trace --node-rank "$RANK" --job "${BACKEND}_ref_build_trace_01" \
  --build-dir "$TRACE" --output "$ROOT" --gid-index "$GID"
```

成功标志BUILD_PASSED或VERBS_BUILD_PASSED。构建任务数1；构建前包含compile-only precision probe和CPU模拟。编译失败停止，按第6节收集构建日志，不更改manifest或用旧模块跳过检查。事件模块应get_reference_events_version=1、get_gpu_stage_version=0，普通模块两个值均0。

## 4. 定义运行及自动收集函数；先做128-token验证

两端各执行一次以下函数定义。每次run先做本地check；即使run失败，也先收集已产生的日志再返回失败。

```bash
run_case() {
  local step="$1" job="$2" build="$3" tokens="$4" rc=0
  bash test/reference_events/run.sh --action run --backend "$BACKEND" \
    --step "$step" --node-rank "$RANK" --master-addr 141.61.17.202 \
    --job "$job" --build-dir "$build" --tokens "$tokens" \
    --output "$ROOT" --gid-index "$GID" --port 32880 || rc=$?
  "$PY" test/gpu_stage_recovery/collect.py "$ROOT/$job" \
    --node-rank "$RANK" --base-build "$BASE" --trace-build "$TRACE" \
    --destination "$DEST/$job"
  return "$rc"
}

run_case trace "${BACKEND}_ref_smoke128_01" "$TRACE" 128
```

202先启动run_case，180秒内在204启动相同JOB。预热1、采样3；每端仅两个Dispatch采集，共6个event窗口。应有CASE_END normal rc=0、reference_events=True，以及REFERENCE_EVENT_COMMAND_PASSED。采样期间独立时钟线程以SCHED_IDLE更新映射主机页，结束后同步、停止线程并释放页。

缺任一事件、溢出、无时钟更新、缺完整CPU请求链或错误epoch会失败；这些是采集完整性检查，不能只看计算正确性通过。失败停止并提供已收集文件，不继续正式任务；不自动重试。若系统异常，保留当前现场及已有日志。

原24组正确性测试也会开启GPU事件点，结束后产生REFERENCE_CORRECTNESS_EVENTS_PASSED及reference_correctness.json，再关闭正确性窗口、进入性能采样。正确性窗口只保存计数和时钟统计，不用于阶段时延分析；性能采样窗口单独reset并严格检查无丢失。

## 5. 验证通过后，4096-token正式三项

两端每次使用相同JOB，同一时间只运行一项。确认上一项两端均成功后，再启动下一条：

```bash
run_case baseline "${BACKEND}_ref_base_01" "$BASE" 4096
```

```bash
run_case trace "${BACKEND}_ref_trace_01" "$TRACE" 4096
```

```bash
run_case post "${BACKEND}_ref_post_01" "$BASE" 4096
```

base/post每点预热50、采样50，丢首样本后49个有效样本。trace每点预热5、采样10；Dispatch两点各10个采集窗口，丢首样本后各9个有效窗口。layout/Combine仍用同轮设置计API时间。时钟分配/更新启停、GPU统计reset/read、CPU记录导出在CUDA Event计时外；逐样本同步及记录操作仍会影响执行状态，因此必须检查相对基线扰动。

避免覆盖已有任务，重跑时两端一起改JOB尾号。期间不切换源码或用户态运行库。

## 6. 需要提供的结果

提供以下目录，每项均需node0/node1、console及collector生成的build日志目录：

```text
test/reference_events/results/<backend>_ref_smoke128_01/
test/reference_events/results/<backend>_ref_base_01/
test/reference_events/results/<backend>_ref_trace_01/
test/reference_events/results/<backend>_ref_post_01/
```

必须保留event_trace.jsonl、reference_correctness.json、API原始samples与summary、status/bench/runtime/setup、模块manifest、冻结benchmark、GPU/内核快照、build/host_tests/precision_probe日志。clock_stats保存在每个event窗口内。无需.so、objects；不需要重传旧trace结果。

构建或最开始check失败，也可单独收集已产生文件（JOB填写失败任务）：

```bash
JOB="${BACKEND}_ref_build_trace_01"
"$PY" test/gpu_stage_recovery/collect.py "$ROOT/$JOB" \
  --node-rank "$RANK" --base-build "$BASE" --trace-build "$TRACE" \
  --destination "$DEST/$JOB"
```

## 7. 两端齐全后汇总（也可以直接交付原始结果，由开发端汇总）

```bash
"$PY" test/reference_events/summarize.py \
  --baseline "$DEST/${BACKEND}_ref_base_01" \
  --trace "$DEST/${BACKEND}_ref_trace_01" \
  --post "$DEST/${BACKEND}_ref_post_01" \
  --output "$DEST/${BACKEND}_ref_summary_01"
```

成功标志REFERENCE_EVENT_SUMMARY_PASSED。只有usable=True才表示两端Dispatch通过大扰动筛选：前后漂移、trace相对前后基线均值变化、各组CV均不超过5%。这不是统计显著性检验。Combine/layout单独保留控制项，不用它们代替Dispatch判定。

输出api_controls.csv、gpu_events.csv、cpu_requests.csv、capture_quality.csv、context.json；提供汇总目录即可，不要求服务器自行做跨后端结论。另一个后端在其可用窗口使用同一提交及相同用户态MACA再测，保留内核/设备差异。

### 解读边界

- GPU读到的是周期更新的主机时钟值；clock_stats用于检查更新间隔/调度异常，不保证每个GPU时间戳具有纳秒精度。clock64前后括号保留读取代价的原始cycles。
- CLOCK_MONOTONIC_RAW只在本机内比较，禁止直接相减202与204的时间戳。
- CPU请求链按proxy/wr_id分组，保留未匹配比例；GPU没有逐请求wr_id，不能强行做一一配对。
- CPU窗口包含该Dispatch API内的notify/control和payload请求，保留payload_bytes以便区分。控制台原有tuning的notify占位值不是GPU阶段时间，分析应使用原始samples和event_trace。
- post→CQE含传输、排队和轮询观察延迟；CQE不等于对端GPU已消费。事件2也不是Dispatch整体结束。
- post_begin/end按逻辑WR记录批量SDK调用边界，不能当成单WR的独占SDK开销；同时保留physical_posts和endpoint_count。
- 不把并行范围或不同SM的clock64值直接相加/相减，不能将这些事件冒充完整、可相加的关键路径分解。

本轮目标是在可信的时间轴上比较首次GPU提交、CPU处理链和远端数据可见进展，进一步缩小候选。若扰动仍超门槛，保留结果并停止归因，不要求原样重复测试。

### 开发端验证范围

已完成URMA/verbs原有Python回归、原始窗口校验和双后端汇总的合成数据测试；CPU模拟覆盖真实采集代码的映射页生命周期、分配失败、epoch/reset、溢出和并发CPU窗口；Bash入口及本文命令已做语法和参数转发检查。开发端没有MetaX编译器和GPU，实际CUCC编译、DMA映射及打开打点后的正确性仍以第3、4节服务器结果为准。
