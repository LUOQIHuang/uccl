# Normal通知FADD：进一步定位完成推进等待

## 1. 本轮明确要回答的问题

此前204的WRITE完成观测→对应FADD完成观测约0.63ms，verbs约4μs。现有时间戳无法分清下面四项。本轮新增**本端FADD返回数据可见**的观测，并记录SDK轮询检查点，判断等待主要位于返回数据到本端之前，还是之后。

| 原候选环节 | 本轮能取得的证据 | 仍不能直接测出的内容 |
| --- | --- | --- |
| 设备依赖/排队 | 如果返回值很晚才可见，它仍是返回前区间的候选 | 设备何时取走、何时解除fence/STRONG依赖 |
| 远端原子推进 | 返回旧值已经到本端，说明已经取得该FADD的返回数据 | 远端原子执行开始/结束、远端GPU实际看见tail的时间 |
| 响应与完成记录生成 | 可测返回值已在本端可见，但FADD CQE仍未被SDK返回的跨度/界限 | 响应到达与物理CQE发布的精确设备时间 |
| CPU发现完成 | 用SDK调用检查点限制“CPU长时间没有轮询”的解释；另测SDK返回→逐WR处理 | 物理CQE刚变可读→CPU发现的精确时间 |

**本轮不宣称直接测出了四项独立的硬件时间。** 最有价值的新分界是R：FADD返回的旧值在本端主机缓冲区中被CPU读到。若R很早，而后面仍有大量SDK空返回，就不能再把整段延迟都解释为远端atomic执行慢；若轮询检查点间隔始终很短，也不能归结为CPU连续数百μs没有调用轮询。

## 2. 打点位置、与LL参考的关系

已核查参考分支940f7c7b的`internode_ll.cu`、`gpu_stage_trace.cuh`和`cpu_stage_trace.hpp`。LL的六个事件是发送准备/首发/接收进展等GPU边界，不是设备内部时间戳。本轮复用其有界记录、epoch分窗、线程本地缓存和窗口外导出思路；**不启动GPU采样主机时钟线程，不再增加GPU mapped-memory读钟点**。

URMA原FADD已有独立请求槽对应的主机返回缓冲区`atomic_operands_`；原代码在提交前将槽置0，设备随后写回远端旧值。本轮只读这些现有位置，**不改初始化、不写哨兵、不新增远端marker，不修改WRITE/FADD/fence/STRONG、窗口或回收策略**。

verbs的原生FADD scratch在不同批次/QP间复用，且不保证每条请求前置0，因此不能把其中变化归属到单条FADD。**verbs仅记录同口径CQE与轮询时序，不采共享scratch、不重新分配返回槽或修改SGE。返回值前/后分解只在URMA端有效**；verbs报告会明确标记该项不可用。

选择每个Proxy的ring0、BF16第6条大WRITE及其相邻通知FADD。两端各4个Proxy，每窗口4对；9个有效窗口共36对/端。第6条而非首条，是因为它对应之前集中长尾位置，且tail旧值应非零，便于区别原始0和返回值。不能将36对当36轮独立benchmark，也不能推广成所有通道的完整关键路径。

```text
WRITE所在SDK poll返回 W
        │
        │ 依赖/排队、远端atomic、返回数据传输：仍合并
        ↓
CPU观察到现有FADD scratch从0变为非零 R（保存最后0/首次非0的时间区间）
        │
        │ 返回数据已经本端可见；可能仍等CQE交付、队列推进、SDK或CPU发现
        ↓
FADD所在SDK poll返回 A
        ↓
CPU处理该条完成记录
```

每64次相关SDK poll采样一次返回缓冲区和poll起止；正返回的SDK poll均记录返回时间。每Proxy缓存有上限，溢出判失败。通过同一CQ、同一endpoint、相邻逻辑WR编号及`payload_bytes=通知增量×14352`核对BF16配对。

R是CPU对本端返回数据的观测，有采样不确定区间；不是远端执行时间。首次看到R若已晚于A，不把A强填为返回值可见时刻，而标为“未在CQE前观察到”，保留全部记录和覆盖数。返回值一直0或前后不同也保留为不可用标记。

W/A使用SDK返回批次边界，避免逐条解析CQE造成的额外偏移；与旧报告逐WR处理打点略有边界区别，不能要求数值精确重现659.75μs。另保留SDK返回到逐WR处理的时间，检查CPU处理批次本身的影响。

## 3. 与正在运行的tokens扫描隔离

**主仓库的GPU代码、传输代码、现有测试驱动和tokens脚本均不修改。** 新入口会创建独立的detached Git工作区，固定基线`da574d61`，仅在该工作区叠加CPU探针。两端自动验证同一套插入位置和源文件SHA；现有tokens扫描继续使用原模块和原源码。

不要把新模块用于旧tokens任务，也不要复用旧BUILD。可先准备独立工作区；正式构建、检查与GPU测试等当前tokens扫描结束后进行，不同时占用GPU。准备工作不改驱动、PCIe或内核，也不切换当前Git分支。

插入涉及独立工作区内以下文件：

| 文件 | 修改 |
| --- | --- |
| `ep/src/urma_transport.cpp` | SDK提交前/后、SDK poll前/后、完成上下文释放前的CPU探针 |
| `ep/include/verbs_stage_diag.hpp` | 同口径verbs提交/poll钩子，保留原请求与标志 |
| `ep/src/uccl_ep.cc` | 三个探针控制/版本接口 |
| 新`ep/include/fadd_path_probe.hpp`、`fadd_path_verbs.hpp` | 只读返回值采样、有限CPU记录 |
| `test/reference_events/event_runtime.py` | 仅BF16性能窗口开关新CPU探针，计时后导出 |
| `test/urma_mode_parity/support.py`、`test/verbs_baseline/common.py` | 记录探针模块版本 |
| 两后端`driver.py` | 将探针开关加入双端配置核对，防止一端开、一端关 |

`internode.cu`、`internode_ll.cu`及其GPU算法不改。源码叠加器在修改独立工作区前，先检查所有替换位置，不允许未知版本“尽量套补丁”。

## 4. 两端环境设置

先测当前可用后端。URMA要求`6.6.0-gdr_f00955680_diag`；verbs要求`6.6.0-145.3.34.165.oe2403sp3.aarch64`。各端1张C500 GPU0，Normal BF164096、hidden7168、experts256、topk8、SM24、chunk32、NVL4；窗口8、URMA fence on、STRONG、WRITE RELAX、ordered保持不变。

两端执行，只有RANK不同：

```bash
cd /home/h00918771/uccl-ep-xpu-integration-new
set -euo pipefail
export VERBS_DIAG_HOME=/home/h00918771
export URMA_DIAG_HOME="$VERBS_DIAG_HOME"
export VERBS_DIAG_PYTHON="$VERBS_DIAG_HOME/metax_dev/venv-maca-py312/bin/python"
export URMA_DIAG_PYTHON="$VERBS_DIAG_PYTHON"
export FADD_PATH_PYTHON="$VERBS_DIAG_PYTHON"
export MACA_PATH=/opt/maca-3.9.0
export PYTHONDONTWRITEBYTECODE=1

RANK=0                     # 202设0，204设1
BACKEND=urma               # RDMA环境改为verbs
JOB=fadd_path_01
TREE="$VERBS_DIAG_HOME/metax_dev/fadd_path_src_v1"
BUILD="$VERBS_DIAG_HOME/metax_dev/fadd_path_build/${BACKEND}_v1"
ROOT="$VERBS_DIAG_HOME/metax_dev/fadd_path_results"
DEST="$PWD/test/fadd_path/results"
PY="$FADD_PATH_PYTHON"
uname -r
mx-smi
```

URMA再设置已经验证的真实私有SDK目录；已有额外运行库设置继续沿用：

```bash
export URMA_DIAG_LIBRARY_DIR="$URMA_DIAG_HOME/metax_dev/urma_after_kernel_20261029/diag/umdk_private_618957c3/lib"
export VERBS_DIAG_LIBRARY_DIR="$URMA_DIAG_LIBRARY_DIR"
export URMA_DIAG_INCLUDE_DIR="$URMA_DIAG_HOME/metax_dev/urma_sdk_private_include"
test -f "$URMA_DIAG_INCLUDE_DIR/urma_api.h"
test -d "$URMA_DIAG_LIBRARY_DIR"
```

## 5. 准备独立工作区、构建、检查

两端分别执行，不需要同时启动。TREE必须是新路径，且位于当前仓库外；不要手动改独立工作区的源码。

```bash
bash test/fadd_path/run.sh --action prepare \
  --backend "$BACKEND" --node-rank "$RANK" --job "$JOB" \
  --tree "$TREE" --build-dir "$BUILD" --output "$ROOT" --destination "$DEST"
```

应看到`FADD_PATH_PREPARE_PASSED`。重复prepare只验证一致性，不重新覆盖源码。若提示源树或bundle改变，保留旧目录，使用新TREE和BUILD，不删除旧证据。

当前tokens扫描结束后，构建新模块：

```bash
bash test/fadd_path/run.sh --action build \
  --backend "$BACKEND" --node-rank "$RANK" --job "$JOB" \
  --tree "$TREE" --build-dir "$BUILD" --output "$ROOT" --destination "$DEST"
```

应看到`FADD_PATH_BUILD_PASSED`。构建并行数1，原host tests及编译探针仍执行；它们不是GPUbenchmark通过的证明。

```bash
bash test/fadd_path/run.sh --action check \
  --backend "$BACKEND" --node-rank "$RANK" --job "$JOB" \
  --tree "$TREE" --build-dir "$BUILD" --output "$ROOT" --destination "$DEST"
```

应看到`FADD_PATH_CHECK_PASSED`。SDK、设备或权限预检查失败时停止，提供现有日志，不跳过错误直接测试。

## 6. 正式测试：只有三项，不做优化开关扫描

202先启动，180秒内在204启动同一命令，RANK分别0/1：

```bash
bash test/fadd_path/run.sh --action run \
  --backend "$BACKEND" --node-rank "$RANK" --job "$JOB" \
  --master-addr 141.61.17.202 --port 33980 --gid-index 3 \
  --tree "$TREE" --build-dir "$BUILD" --output "$ROOT" --destination "$DEST"
```

自动顺序：

1. `pre`：关闭CPU探针的4096基线。
2. `probe`：仅BF16 Dispatch开启新CPU探针；其他测点仍是控制项。
3. `post`：关闭CPU探针的4096回测。

三项使用同一模块、同一boot和库，各点5次预热、10次计时，丢首值后9个有效样本。每项保留原有24组正确性检查；不额外跑LL或128-token smoke。底层仍是原Normal基准程序。

最终两端都应看到`FADD_PATH_RUN_PASSED`。失败自动收集已有文件后停止，不自动重试；不要为获得理想图形重复整个实验。

## 7. 需要提供的目录

```text
test/fadd_path/results/fadd_path_01/<urma或verbs>/
  overlay_node0.json / overlay_node1.json
  capabilities_node0.json / capabilities_node1.json
  cases/fadd_path_01_<backend>_build/...
  cases/fadd_path_01_<backend>_check/...
  cases/fadd_path_01_<backend>_pre/...
  cases/fadd_path_01_<backend>_probe/...
  cases/fadd_path_01_<backend>_post/...
```

提供完整后端子目录，两端node0/node1、console、已收集的build日志都保留。新核心文件是probe内的`fadd_path.jsonl`，另外必须保留API原始samples/summary、bench/status/runtime/setup、benchmark副本、host/GPU/内核快照、manifest和overlay来源。collector不收.so、object或cache/tmpdir。

手动中断后可补收，命令只收集已有文件，不运行GPU：

```bash
bash test/fadd_path/run.sh --action collect \
  --backend "$BACKEND" --node-rank "$RANK" --job "$JOB" \
  --tree "$TREE" --build-dir "$BUILD" --output "$ROOT" --destination "$DEST"
```

## 8. 汇总与明确的正负判据

两端文件齐全后，可在服务器或开发端执行，无需画图库：

```bash
"$PY" test/fadd_path/analyze.py \
  --root "$DEST/$JOB" --backend "$BACKEND" \
  --output "$DEST/$JOB/summary_${BACKEND}"
```

输出目录必须不存在。生成`REPORT.zh-CN.md`、`api_controls.csv`、`pairs.csv`、`bounds.csv`、`decision.json`和`context.json`。三项源码/模块/boot/库、采集完整性及四Proxy覆盖必须一致；不能用不完整记录拼结论。

先检查BF16探针相对前后基线影响、基线漂移和CV均≤5%。失败则保留原始数据，仅描述观察器运行，不继续无条件重测。**整体门槛通过也不能保证每个内部区间完全零扰动**。

通过后按以下顺序判断：

| 看见什么 | 支持的判断 | 不支持的跳跃 |
| --- | --- | --- |
| 相邻SDK轮询检查点最大间隔远小于W→A | 不能用同量级的连续“CPU没轮询”解释全部等待 | 不能直接认定设备atomic慢 |
| R明显早于A，之后SDK仍返回0 | 返回数据已本端可见，等待还在完成交付/SDK路径继续 | SDK返回0不等于物理CQE内存一定没就绪，可能涉及provider/队列/可见性 |
| R→A占较大区间，轮询检查点密集 | 优先审查本端完成生成/交付/provider推进 | 不能直接分出CQE生成与provider内部各占多少 |
| W→R较长，R→A较短 | 返回前链路仍是重点，包括依赖、远端atomic和响应传输 | 不能再把这三者未经观测拆成精确百分比 |
| R只在CQE处理时才看到，或一直0/前后不一致 | 返回值标记受限；仍可使用轮询服务记录，但不输出假的前后分解 | 不能补0或把CQE时刻冒充返回值就绪时刻 |

最大检查点间隔包含未记录的64次内SDK调用及CPU工作，因此是保守上界，不是精确CPU调度时长。最后空轮询到FADD返回也只给保守尾部区间，不是精确“CQE已就绪后的CPU发现时间”。返回值前/后给上下界；独立中位数不相加成API，也不把不同数量的有界子样本直接当整体占比。

SDK调用序号逐次递增，正返回批次全部保存，因此还可统计W→A之间实际调用次数及空返回次数；只有空调用的时间戳按64次采样。次数不等同调用占用CPU的累计时间。

若后续仍落在W→R，真正拆开前三项需要匹配私有UMDK/provider的WQE消费/依赖解除、远端atomic、响应/CQE发布观测。自动保存的capabilities文件只记录现有头文件与可见tracepoint，**不会开启内核trace或更改设备**；参考LL六点本身不提供这些时间戳。

本地验证包括纯CPU记录器、正反完成顺序及已回收scratch不再读取、采样界限与删失、源码叠加位置与Python语法、实际传输代码对SDK/verbs头文件的CPU编译回归。没有本地MetaX GPU，不宣称本轮已在实机验证。
