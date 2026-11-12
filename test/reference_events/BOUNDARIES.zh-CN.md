# 本轮优先：拆解Dispatch持续发送、通知等待、epoch可见性及输出收尾

本轮4096-token结果已接收，见[BF16分析与FIFO重建证据](results/urma_boundaries_01_fifo_audit/ANALYSIS.zh-CN.md)。已完成的smoke及三项不必重跑；当前下一步为[单变量窗口因果测试](WINDOW_CAUSAL.zh-CN.md)。下文保留本轮原始测试协议。

## 本轮只回答什么

已知202 BF16的首次WRITE→最后远端token可见区间，verbs约6.428ms、URMA约7.110ms；204 URMA的完成长尾集中在每通道第6块大WRITE。本轮沿这个已观测到的区间补边界，判断延迟落在后续数据准备/发布、CPU请求推进、末块通知等待、末token epoch可见性，还是输出收尾。

**本轮不运行窗口8→4、混合提交、强弱序或LL实验。**两端默认窗口8，Normal WRITE RELAX、FADD强序和原fence、completion all、lane-mixed均保持原值。原始kernel语句/分支/屏障/内存操作未改，只添加受控事件；主机测试会剥离标记后与原始kernel逐字核对。

只构建一个新模块。先128-token验证，再跑4096-token的paced→trace→post三项。旧六流事件接口保留；本轮使用独立12流记录结构及新能力检查，不允许旧.so与新结构混用。开发机没有MetaX环境，主机模拟通过不代表实机GPU已经验证。

## 补了哪些具体边界

仍使用参考代码的采样RAW主机时钟、每条记录的clock64读钟前后值；没有恢复密集RAII区间统计。

| ID | 位置/角色 | 能回答的问题 |
| --- | --- | --- |
| 0 | Sender首token处理前，旧点保留 | 首批准备参照 |
| 1 | Coordinator首次远端WRITE调用前，旧点保留 | 持续发送区间起点 |
| 3/4 | 最后远端WRITE调用前 / WRITE及对应tail通知发布后 | 最后数据何时准备好？最终命令发布是否阻塞？ |
| 10/11 | 第6块远端WRITE调用前 / 对应通知发布后 | 已知长尾位置在GPU发布前还是发布后？ |
| 5/6 | Forwarder末块tail检查开始 / 检查成功 | 末块通知的推进是否拖后？ |
| 7/2 | 最后远端token的epoch检查前 / epoch可见，ID2为旧点 | tail已可见后，是否仍等待payload数据的新epoch？ |
| 8 | Forwarder完成转发、发布尾指针并退役后 | 数据可见后，转发/NVL背压是否继续拖后？ |
| 9 | Receiver完成输出代码及本角色清理后 | 剩余接收、输出及收尾是否拖后？ |

0/1/2没有改成新含义。3/4、10/11是GPU命令发布边界，不是NIC真正发送或完成。第6块按32-token chunk的第6次payload发布选中，不包括16B元数据WRITE。

事件5每个末块检查轮次最多记录一次，不在每次spin里写日志。若先处理了其他源，会再次进入检查轮次：导出同时保留首次5→6总跨度、最后5→6成功轮次跨度，前者含中间round-robin工作，不能叫纯等待。128-token通常没有第6块，允许ID10/11为0；4096正式测试要求全部12点覆盖。

CPU保留poll→decoded→prepare→post_begin→post_end→cqe→ack_slot记录。仅本profile额外每128次SDK CQ/JFC轮询记录一个心跳，不逐次记录空poll。心跳是软件服务证据，不是硬件CQE到达时间。

## 如何保证能用，又不让观察器阻断性能

- 每项真实Normal正确性检查保留；新增点在正确性测试期间也开启。
- 128-token只验证正确性、ABI、0～9点覆盖，不用于回退幅度结论。
- 正式三项同一个新模块、同一源码/诊断脚本/boot/运行库、同一固定形状及逐样本同步协议。每点预热5次/采样10次/丢首值，9个有效值。
- paced与post关闭GPU/CPU采集及主机时钟；trace开启。Dispatch的前后漂移、trace相对两控制均值变化、各组CV均须不超过5%，才将阶段数据视为具有代表性。
- 若扰动超限，工具仍导出原始观测并标usable=false；保留两个关闭采集结果，不把异常观测包装成根因，不自动展开更多采样/打点矩阵。
- 若GPU采样时间早于CPU窗口origin或时钟线程调度报错，也标usable=false；不删样本，context分别保留性能代表性与时钟偏移检查结果。最大更新间隔包含启动/停止空隙，不能直接当作每个事件的误差界。
- 若运行/正确性失败，立即停止、收集，不重启、不改PCI配置、不清理他人进程。已经完成的性能样本仍保留，不为补文件重新跑benchmark。

## 1. 两端设置URMA环境

202设RANK=0，204设1。先拉取本轮代码；两端参数除RANK外相同：

```bash
cd /home/h00918771/uccl-ep-xpu-integration-new
set -euo pipefail
export URMA_DIAG_HOME=/home/h00918771
export URMA_DIAG_PYTHON=/home/h00918771/metax_dev/venv-maca-py312/bin/python
export MACA_PATH=/opt/maca-3.9.0
export URMA_DIAG_LIBRARY_DIR=/home/h00918771/metax_dev/urma_after_kernel_20261029/diag/umdk_private_618957c3/lib
export URMA_DIAG_INCLUDE_DIR=/home/h00918771/metax_dev/urma_sdk_private_include
# 额外运行库沿用上一轮URMA成功设置，不混入旧MACA库。
BACKEND=urma
RANK=0  # 204改为1
BUILD=/home/h00918771/metax_dev/reference_event_build/urma_boundaries_v1
ROOT=/home/h00918771/metax_dev/reference_event_results
JOB=urma_boundaries_01
DEST="$PWD/test/reference_events/results"
CONSOLE="/home/h00918771/metax_dev/boundary_console/$JOB/node$RANK"
mkdir -p "$CONSOLE"
uname -r
mx-smi
```

目的：固定环境。使用已协调空闲的GPU0、每端一张C500；预期6.6.0-gdr_f00955680_diag、MACA3.9.0.29/KMD3.10.14、udmac0d1e2、enp33s0，Atomic Request/runpm/私有库沿用已成功的URMA配置。脚本只检查，不主动切核或更改硬件配置。

## 2. 两端各构建一次新模块

本轮新增GPU事件及独立ABI，必须新BUILD；不能复用旧events/window/submit模块，也不要修改manifest。paced/trace/post全用这个模块：

```bash
bash test/reference_events/run.sh --backend "$BACKEND" \
  --reference-profile boundaries --action build --step trace \
  --node-rank "$RANK" --job "${JOB}_build" --build-dir "$BUILD" --output "$ROOT" \
  2>&1 | tee "$CONSOLE/build.log"
```

目的：验证新增代码能在实际MetaX编译器及私有SDK下构建。先运行compile-only precision probe及CPU模拟，再构建扩展，不启动benchmark。成功为BUILD_PASSED或BUILD_ALREADY_PASSED。

构建失败提供CONSOLE/build.log以及BUILD下build.log、host_tests.log、request/manifest和precision_probe相关日志，停止，不必继续下面任务。不需要.so/objects。

## 3. 两端先做128-token验证

202先启动，180秒内204启动相同JOB；33180/33181端口需空闲：

```bash
bash test/reference_events/run_boundaries.sh --part smoke \
  --backend "$BACKEND" --node-rank "$RANK" --master-addr 141.61.17.202 \
  --build-dir "$BUILD" --job "$JOB" --output "$ROOT" --destination "$DEST" \
  --port 33180 \
  2>&1 | tee "$CONSOLE/smoke.log"
```

目的：在正式采集前发现GPU ABI、事件位置或正确性问题。内部check会要求reference_events_version=1、reference_boundary_version=1、reference_profile=boundaries、normal_max_inflight=8。真实测试预热1/采样3；应有原正确性通过、reference_correctness=True、reference_events=True、CASE_END normal rc=0，最终BOUNDARY_SUITE_PASSED part=smoke。

两端均通过后再继续。某端失败时保留自动收集结果和CONSOLE，不继续4096；check通过本身不等于benchmark通过。

## 4. 正式4096-token三项

下面一条命令按paced→trace→post顺序执行，不并行；会核查本端smoke状态通过：

```bash
bash test/reference_events/run_boundaries.sh --part formal \
  --backend "$BACKEND" --node-rank "$RANK" --master-addr 141.61.17.202 \
  --build-dir "$BUILD" --job "$JOB" --output "$ROOT" --destination "$DEST" \
  --port 33180 \
  2>&1 | tee "$CONSOLE/formal.log"
```

目的：测阶段边界，并同时检验采集影响。固定4096 tokens、hidden7168、experts256、topk8、SM24、Dispatch chunk32、NVL chunk4；其余参数不扫描。保留FP8/BF16 Dispatch、layout、Combine12/16五项API计时；新增GPU事件只在两个Dispatch测点采集。成功为每项CASE_END normal rc=0，最后BOUNDARY_SUITE_PASSED part=formal。

同名结果会拒绝，不覆盖；失败仍收集已有文件然后停止。若需要重跑，使用新JOB，不修改旧结果。

## 5. 提供这些目录和日志

DEST下4个完整目录，必须包括两端node0/node1及自动收集的console/build：

```text
urma_boundaries_01_smoke128/
urma_boundaries_01_paced/
urma_boundaries_01_trace/
urma_boundaries_01_post/
```

原始event_trace.jsonl、api_samples/sample_summary、reference_correctness/reference_control、status、bench.log、setup/preflight/runtime/host、模块manifest、GPU/kernel快照均保留；不要只给汇总截图，不删首样本或离群点。另提供CONSOLE下build.log、smoke.log、formal.log；构建或运行失败时提供当次已有目录即可。

目的：核实正确性、覆盖、同源对照、采集影响和具体等待位置，避免再次只有整体均值。

两端目录齐全后的可选离线汇总（不需GPU，输出目录须不存在）：

```bash
"$URMA_DIAG_PYTHON" tools/reference_events/summarize_boundaries.py \
  --control "$DEST/${JOB}_paced" --trace "$DEST/${JOB}_trace" \
  --post "$DEST/${JOB}_post" --output "$DEST/${JOB}_summary"
```

输出重点：

- api_controls.csv：关闭采集前后漂移、采集影响及代表性门槛。
- boundary_spans.csv / boundary_medians.csv：按节点、负载、窗口、通道/角色记录上述边界跨度；同角色还保留原始device cycles及读钟开销。
- cpu_chunk6_and_last.csv：第6块及最后一块WRITE，按同通道和发布序号关联GPU就绪/发布边界及CPU处理、提交、完成、回执；保留心跳是否完整夹住请求、观测到的最大心跳间隔。
- gpu_events.csv / cpu_requests.csv / cpu_stage_medians.csv等：保留原始导出和完整CPU链；capture_quality/context保留丢记录、时钟及环境信息。

## 结果如何回答定位问题

| 观察 | 优先指向的代码区间 |
| --- | --- |
| 首次WRITE到最后payload就绪很长 | 后续数据准备、发送缓冲区腾空/反馈、Coordinator持续推进；不能直接称为GPU纯计算 |
| 第6块或末块就绪→命令和通知发布很长 | GPU命令发布函数、FIFO背压及其fence/协调路径 |
| GPU就绪后CPU很晚发现，或CPU发现后很晚提交 | GPU→CPU命令通路、Proxy收集/准备/排队；按通道和序号匹配，非新增协议携带的严格WR标识 |
| 提交→CQE很长，且完整夹住该请求的心跳最大间隔很短 | 不支持Proxy长时间完全不轮询，优先审查提交后依赖、设备/网络与对端访问；仍非纯传输计时 |
| 末块tail检查很长 | tail通知推进及这个Forwarder检查循环；区分首轮总跨度与最终成功轮次 |
| tail已可见但末token epoch检查很长 | 末payload数据可见性/顺序区间 |
| 最后token可见后Forwarder或Receiver迟迟不结束 | 转发/NVL空间背压、接收输出及收尾路径 |

心跳大间隔不等于CPU停顿：它还包含128次轮询和之间的工作。只有完整覆盖请求的短间隔，才能排除同等量级的长服务空档。短GPU跨度可能低于采样主机时钟分辨率；保留cycles供核查，不把读数0当成无等待。

各角色并行，本机发送与本机接收也对应相反方向。**不跨机器相减绝对时间戳，不把本机发送完成与本机接收拼成同一WR，不将重叠跨度相加为完整关键路径。**Receiver完成点是输出代码路径结束，不能替代整个kernel入口/出口。本轮不为了堆叠图将未知余量命名为某个阶段。

## RDMA同口径入口已准备

本轮先做URMA。以后获得verbs窗口时，无需重新设计打点，使用同一源码和本流程，只改：

```bash
export VERBS_DIAG_HOME=/home/h00918771
export VERBS_DIAG_PYTHON=/home/h00918771/metax_dev/venv-maca-py312/bin/python
BACKEND=verbs
BUILD=/home/h00918771/metax_dev/reference_event_build/verbs_boundaries_v1
JOB=verbs_boundaries_01
```

重新设置CONSOLE为该JOB后，从构建步骤开始。沿用已验证的verbs内核6.6.0-145.3.34.165.oe2403sp3.aarch64、rocep40s0f0、GID3、DMA-BUF及相同MACA3.9；主入口默认GID3，可显式--gid-index 3。两后端先各自通过代表性门槛，再比较同一边界；旧verbs三点数据不能冒充新增边界的对照。
