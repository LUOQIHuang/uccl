# URMA Normal：恢复少量并发，定位长等待触发条件

**`urma_pair_load_01` 四对实验及 URMA 三队列深度对照均已完成。** 两端运行步骤按环境选择。[四对结果分析](results/urma_pair_load_01/ANALYSIS.zh-CN.md) · [RDMA 深度对照](DEPTH12_VERBS.zh-CN.md) · [URMA 窗口步骤](ORDER12.zh-CN.md)

## 1. 目的与已有依据

上一轮 `serial_pair_01` 在URMA环境已经得到：单线程、单数据队列、最多一对WRITE/FADD在途时，单对中位数约37–39 μs，1800个有效请求对均在同次SDK轮询返回两条完成，W→A观测间隔为0。此前完整Normal的对应间隔中位数，202约682.990 μs、204约427.350 μs。

这说明数百μs间隔不是每次URMA原子操作都必然付出的固定代价，但还不能确定是哪种负载条件触发。下一步只做下面三组，不等待RDMA环境，也不追加tokens、排序、绑核等大矩阵。

| 组名 | 活跃数据队列 | 最多在途请求对 | 每次迭代 | 回答的问题 |
| --- | ---: | ---: | --- | --- |
| serial4 | 1 | 1 | 连续完成4对，每对全部完成才发下一对 | 同数据量基线 |
| queue4 | 1 | 4 | 一次提交4对，等全部完成 | 同队列积压能否触发长等待？ |
| lanes4 | 4 | 4 | 每队列1对，全部提交后等全部完成 | 相同4对分到不同队列有何变化？ |

每组固定4条WRITE，各459264B（32 token ×14352B），总1837056B；4条对应FADD，每条加32。不是重新测试上轮1对与2对，而是建立**相同四对数据量**的串行、并行对照。

特别注意：原Normal Proxy对普通WRITE和独立通知FADD的处理是先收集WRITE，再收集FADD。因此queue4沿用 `W0,W1,W2,W3,A0,A1,A2,A3`，不是交替排列。lanes4由原UrmaTransport按数据队列分组，各队列为W/A链。不能仅凭W0→A0变长就判断异常；先比较相同数据量的整批耗时，再看每对请求位置。

## 2. 固定项、记录项与限制

- 只测URMA；内核 `6.6.0-gdr_f00955680_diag`，设备 `udmac0d1e2`，GPU 0。
- 保留生产UrmaTransport、GPU DMA-BUF数据和GPU原子计数器；WRITE RELAX、FADD STRONG、per_request fence、每条请求产生完成。
- 一个工作线程，202固定CPU168、204固定CPU192，沿用上一轮成功绑核。若这些核现已不可用，脚本停止；不要静默改成自动绑核后混为同一对照。
- 三组使用同样大小和布局的主注册区，约46MB；只创建1或4个数据Jetty，各另有1个控制Jetty，共享本线程的完成队列。不是完整Normal的12个活跃数据通道。
- 先202发204收，再204发202收，**不是双向同时发送**。3轮，偶数轮倒序，20次预热、101次采样，丢弃首条，保留100条；每个点均做GPU数据与计数器校验。
- 每条请求记录实际SDK提交开始/结束、完成返回时间、轮询序号；独立保留4个请求位置。记录整批SDK跨度、C++循环跨度、SDK轮询次数和实际最大未完成请求对数。
- 三组只还原少量负载，不含原GPU生产/消费kernel、命令FIFO、Proxy主循环、head反馈、持续双向负载。GPU校验、TCP同步、JSON输出在计时外。

新测试在独立源码树上叠加，固定基线 `fed2a437`，保存文件哈希。原串行入口默认行为保持不变；当前工作仓库的生产源码不直接改动。

“在途”按本端尚未取得该对全部完成计数，不代表设备内部同时执行了几条请求；设备实际并行度不能由这个软件计数直接测出。

## 3. 两端准备

先取得本次更新，在仓库根目录操作。以下按本人目录 `/home/h00918771`，不要使用他人目录。两端分别设置；若已迁移过的私有SDK位置不同，只修改相应两条目录变量。

```bash
export URMA_DIAG_HOME=/home/h00918771
export VERBS_DIAG_HOME="$URMA_DIAG_HOME"
export URMA_PAIR_LOAD_PYTHON="$URMA_DIAG_HOME/metax_dev/venv-maca-py312/bin/python"
export URMA_DIAG_PYTHON="$URMA_PAIR_LOAD_PYTHON"
export VERBS_DIAG_PYTHON="$URMA_PAIR_LOAD_PYTHON"
export URMA_DIAG_LIBRARY_DIR="$URMA_DIAG_HOME/metax_dev/urma_after_kernel_20261029/diag/umdk_private_618957c3/lib"
export VERBS_DIAG_LIBRARY_DIR="$URMA_DIAG_LIBRARY_DIR"
export URMA_DIAG_INCLUDE_DIR="$URMA_DIAG_HOME/metax_dev/urma_sdk_private_include"
export JOB=urma_pair_load_01
export TREE="$URMA_DIAG_HOME/metax_dev/urma_pair_load_src_v4"
export BUILD="$URMA_DIAG_HOME/metax_dev/urma_pair_load_build/urma_v4"
export ROOT="$URMA_DIAG_HOME/metax_dev/urma_pair_load_results"
export DEST="$PWD/test/urma_pair_load/results"
test -x "$URMA_PAIR_LOAD_PYTHON"
test -f "$URMA_DIAG_INCLUDE_DIR/urma_api.h"
test -d "$URMA_DIAG_LIBRARY_DIR"
uname -r
mx-smi
```

202单独执行：

```bash
export RANK=0
export CPU=168
```

204单独执行：

```bash
export RANK=1
export CPU=192
```

MACA及其他运行库沿用上次URMA成功环境。本脚本只检查现状，不更改内核、PCIe Atomic、runpm或驱动。服务器若已不是上述可运行URMA环境，先停止，不跳过检查。

## 4. 准备独立源码、编译、检查

两端定义同一个函数，RANK/CPU按上节不同：

```bash
urma_load() {
  bash test/urma_pair_load/run.sh --action "$1" \
    --backend urma --node-rank "$RANK" --job "$JOB" \
    --master-addr 141.61.17.202 --port 34480 \
    --worker-cpu "$CPU" --rounds 3 --warmups 20 --samples 101 \
    --tree "$TREE" --build-dir "$BUILD" --output "$ROOT" --destination "$DEST"
}
urma_load prepare
urma_load build
urma_load check
```

这三个操作可分别执行，不要求两端同时开始。应依次看到 `URMA_PAIR_LOAD_PREPARE_PASSED`、`URMA_PAIR_LOAD_BUILD_PASSED`、`URMA_PAIR_LOAD_CHECK_PASSED`。build沿用原构建入口，编译并行数1；check检查SDK、GPU原子配置、扩展来源和设备可用状态。

TREE 首次必须不存在且在仓库外。BUILD 使用本套件自己的构建目录。相同代码重复 prepare 仅检查哈希。如果提示 `LOAD_TREE_CHANGED`，保留旧目录，换新的 TREE 和 BUILD。

任何一步失败即停止，保留已收集日志，不继续run。每次新开终端需重新设置变量和函数。

## 5. 正式运行

确保无其他GPU benchmark运行。202先启动，204在180秒内启动：

```bash
urma_load run
```

每端18个点（3种形态×2个方向×3轮），最终看到 `URMA_PAIR_LOAD_RUN_PASSED`；run内的bench.log末尾为 `URMA_PAIR_LOAD_ALL_PASSED`。每个点包含20次预热和101次记录，首条记录不进入统计。全部有效数据为1800个迭代、7200个发送请求对；每个发送方向900个迭代、3600对。接收端也保存状态和校验，但不重复计入发送时延统计。

run会先再做一次只读检查。已有run目录不覆盖，若确需新实验，改JOB；失败不自动重跑。中断后补收已有日志：

```bash
urma_load collect
```

## 6. 需要提供的结果与离线分析

完整保留下面的目录，两端文件都需要：

```text
test/urma_pair_load/results/urma_pair_load_01/urma/
```

包括 `overlay_node0.json/overlay_node1.json`，cases下的build/check/run_gate/run及console。run各node内重点文件为 `samples.jsonl`、`paths.jsonl`（每个点的实际队列数及顺序配置）、`plan.json`、`affinity.json`、`status.json`、`driver_status.json`、`worker_request.json`、`request.json`、`manifest.json`、`preflight.json`、`runtime.json`、`bench.log`、主机/内核快照。失败时也保留完整已产生的目录。无需提供.so、object、cache或独立源码树。

两端文件齐全后，可以在任意有Python3的机器分析，无需GPU：

```bash
"$URMA_PAIR_LOAD_PYTHON" test/urma_pair_load/analyze.py \
  --root "$DEST/$JOB" --output "$DEST/$JOB/summary_urma"
```

输出目录首次必须不存在。`summary_urma/`也保留。若只有一端结果，暂不执行分析，先保留该端文件。

输出包括：

| 文件 | 用途 |
| --- | --- |
| REPORT.zh-CN.md、summary.csv | 三组等量整批耗时、单对时延及稳定性 |
| contrasts.csv | 同轮queue4对serial4、lanes4对queue4的整批增减百分比 |
| positions.csv | 分开列出第0/1/2/3对，查看是否只有链前部请求等待更久 |
| pairs.csv、iterations.csv | 全部有效原始派生记录，间隔有符号，不截掉负数 |
| per_run.csv、context.json | 三轮波动和环境/来源 |

整批SDK跨度定义为首次WRITE所在SDK调用开始→四对WRITE/FADD均被本端取得完成。C++循环跨度还含构造/封装等开销。W→A是同一对请求的本端完成观测间隔，不是远端执行时间；同次SDK轮询返回为0。单对时间可重叠，不能相加当总时延；同次链式提交的WRITE/FADD SDK调用时间也不能相加。

## 7. 如何决定后续，不扩散测试

| 观察 | 可以推进的结论/后续 |
| --- | --- |
| queue4较serial4整批更快，W→A虽增长但无数百μs长尾 | 少量单队列积压未复现原退化，间隔增长可能是正常批内排队；不要据此改fence |
| queue4整批反而明显更慢，且长间隔/长尾复现 | 最小触发条件缩小到单线程单队列多对在途，下一步查此队列依赖和完成交付 |
| queue4慢、lanes4恢复 | 支持同一队列内的顺序/积压因素；尚不能直接区分fence、设备队列或SDK分批行为 |
| lanes4较queue4整批明显更慢 | 多队列端点/共同完成服务可能参与，下一步限定在这个差异内拆解 |
| 三组都快 | 四对负载不足以触发，后续才考虑更深在途、原Proxy、多线程、head反馈；不能宣称排除了并发 |
| 某方向不稳定或正确性失败 | 保留该端环境及日志，先处理该失败，不把混合均值当性能结论 |

稳定门槛为每轮整批跨度CV≤5%、三轮均值跨度/中位数≤5%；超标明确标注，不自动追加实验。即使复现，也还没有RDMA同形态对照，不能仅凭本轮确定“URMA特有”。本轮作用是先找到最小复现条件，缩短后续排查范围。

## 8. 开发端验证范围

开发端已通过Python语法、固定源码叠加位置、计时数据约束、真实TCP双端worker流程和报告完整性检查；实际UrmaTransport/load session使用模拟GPU/provider跑通三组、两个方向、各3次，共18个迭代的数据及计数器校验。原URMA host回归、旧串行测试7项及本文7段Bash语法检查通过。参考SDK测试占位字段不代表私有SDK真实ABI。开发机不能运行MetaX GPU benchmark，服务器build/check及实机正确性仍为必要步骤。
