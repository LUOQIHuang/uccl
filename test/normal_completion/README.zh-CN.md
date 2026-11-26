# 下一项：区分WRITE/FADD完成前后的等待，附CPU排空观察

**当前实机入口在[实际阻塞WRITE采集](BLOCKERS.zh-CN.md)。** 本页记录的是已完成的第6条WRITE/FADD探针实验。

**2026-11-25：本轮已运行，勿重跑。**[结果分析](results/normal_completion_01/ANALYSIS.zh-CN.md)：选中204慢WRITE约2794μs，CQE可读后至SDK交付上界中位数仅4.44μs；CPU QUIET也无毫秒级阻塞。由于本目录.gitignore遗漏，42条元数据必需路径未随结果上传，已修复，只需补交已有JSON/LOG，不重建或重跑。下一项见[分阶段提交操作文档](../reference_events/WRITES_FIRST_BATCH.zh-CN.md)。下文保留本轮历史操作，供核对。

当前只需要URMA环境。此实验不改fence、STRONG、窗口、请求顺序、完成策略或QUIET语义；不添加GPU事件、GPU读取主机时钟或Kineto采集。以当前实现为基线，在隔离worktree中增加只读CPU探针；两端各构建一次，运行pre→probe→post三项。

## 1. 为什么测，以及能验证到哪里

8→32已完成，BF16两配对分别慢1.659%和0.271%，停止扩窗。现有重放表明204第6块/末块被尚无完成观测的大WRITE占住额度，202末块主要由FADD占住；源审查又发现QUIET控制图不等价。本轮不重复证明“等待很长”，而是推进完成链路的边界。

| 要验证的猜测 | 本轮具体观测 | 能排除或支持什么 |
| --- | --- | --- |
| 等待主要发生在CQE变得可读之前 | 目标WRITE/FADD提交时间、目标CQ位置的最后无效及首次有效观测 | 给出应用尚不可读阶段的界限；仍不等于纯网络/设备执行时间 |
| CQE早已有效，但未被SDK交付 | 只读owner位、实际consumer index、SDK返回请求身份和序号 | 能直接观察目标有效却被无效队首挡住；或在有效队首后仍较晚交付 |
| CPU很久才再次服务完成队列 | 每次相关SDK轮询的时间和相邻服务间隔 | 区分“SDK调用内部”与“轮询之间”的长间隔；前者不等于纯CPU计算 |
| URMA真实排空等待占了明显时间 | 完整记录32个QUIET、2个BARRIER的CPU处理区间 | 量出CPU处理中的排空成本；不包括GPU发布/等待命令及异步全局屏障全过程 |

```text
目标WRITE或FADD提交
    → 最后一次观测该CQE无效
    → 首次观测该CQE有效
    → SDK返回该请求
    → Proxy原有回收逻辑
```

最后两个观测之间仍可能包含队首阻塞、SDK处理和CPU服务间隔。没有看到有效瞬间保留“未知”，不填零。目标关联使用SDK实际消费的CQ位置与token，不按提交次序猜位置。

选取已发现长尾集中的第6个BF16大WRITE和相邻tail FADD，**202和204各4个Proxy、ring0**；每端9个保留调用，共36条WRITE和36条FADD目标，不当作72次独立benchmark。仅观察4/12个活跃发送通道，不能据此宣称已经重建全Dispatch关键路径。

本轮不能单独区分设备内部排队、远端访问及链路传输，也没有改变GPU注册映射，因此不声称直接验证所有硬件猜测。先看完成可读边界，再决定是否补匹配的双向纯WRITE对照；不先增加消息大小/并发/强弱序矩阵。

## 2. 固定配置和私有provider约束

沿用已成功环境：`6.6.0-gdr_f00955680_diag`、MACA3.9、GPU0，202=`udmac1d1e2`、204=`udmac0d1e2`。4096 tokens/rank、hidden7168、topk8、experts256、SM24、NVL/RDMA chunk4/32；202数据fence全开，204仅tail-off，窗口8、ordered回收、completion all、WRITE RELAX、FADD STRONG。原Normal正确性检查保留；性能部分只保留BF16 Dispatch。

CQ owner读取复用此前已修复AArch64公共结构尺寸的探针。只适用于归档私有provider：

```text
文件：$URMA_DIAG_LIBRARY_DIR/urma/liburma-udma.so
SHA256：18ea061e598330ba56ac07d7f9b4691414884aaf805c7f7a1973b1bc5c028967
Build ID：a94dc33688722b1604ae5dc2d8f06b783b8e474a
```

setup先检查库文件SHA；实际创建JFC时再核对回调所属库Build ID、SDK ABI、stride/depth/mask。布局不符即停，不能平移偏移、换系统库或关闭检查。每次只读未来16条CQE的owner首字，不写CQ、不推进consumer、不额外消费记录。

## 3. 两端拉取并分别setup

两端：

```bash
cd /home/h00918771/uccl-ep-xpu-integration-new
git pull --ff-only https://github.com/LUOQIHuang/uccl.git codex/metax-urma-integration
git rev-parse HEAD
```

两端HEAD一致。默认路径已写进入口：

```text
Python： /home/h00918771/metax_dev/venv-maca-py312/bin/python
MACA：   /opt/maca-3.9.0
UMDK：   /home/h00918771/metax_dev/urma_after_kernel_20261029/diag/umdk_private_618957c3/lib
SDK：    /home/h00918771/metax_dev/urma_sdk_private_include
TREE：   /home/h00918771/metax_dev/normal_completion_src_v1
BUILD：  /home/h00918771/metax_dev/normal_completion_build/urma_v1
OUTPUT： /home/h00918771/metax_dev/normal_completion_results
```

本轮有新C++只读探针，**需要一次新构建**，不能复用normal_scope或credit测试的.so。旧构建保留。worktree固定生产基线0c818748，再叠加本轮CPU探针及BF16性能入口；GPU kernel源码不改。复制和构建不覆盖当前主仓库源码。

202执行：

```bash
bash test/normal_completion/run.sh --action setup --node-rank 0
```

204执行：

```bash
bash test/normal_completion/run.sh --action setup --node-rank 1
```

setup不需要两端同时启动，依次创建隔离源码、编译、执行原有CPU transport检查及扩展预检。应各自出现`NORMAL_COMPLETION_SETUP_PASSED`。本地已验证Python锚点及记录器主机模拟；真实私有SDK/AArch64编译由这里验证。

若路径不同，沿用已成功环境变量URMA_DIAG_HOME、URMA_DIAG_PYTHON、MACA_PATH、URMA_DIAG_LIBRARY_DIR、URMA_DIAG_INCLUDE_DIR。网卡可用URMA_DIAG_IFNAME覆盖，默认202=enp40s0、204=enp34s0f5u1u4；GPU PCI默认0000:21:00.0，可用URMA_DIAG_GPU_PCI覆盖。TREE/BUILD/OUTPUT等也可传对应参数，后续setup/run必须保持相同参数。

若此前已有不同内容的同名TREE，使用新的`--tree ..._v2 --build-dir .../urma_v2`，不要删除旧树、手改marker或manifest。setup失败时提供已收集日志和终端错误，不反复重编译。

## 4. 两端同步运行三项

协调两端GPU/URMA可用，33780/33781端口空闲，不并行跑其他benchmark。202先启动，204尽量在60秒内启动。

202：

```bash
bash test/normal_completion/run.sh --action run --node-rank 0
```

204：

```bash
bash test/normal_completion/run.sh --action run --node-rank 1
```

脚本自动先重做本地check，再顺序运行：

| case | 202采集 | 204采集 | 用途 |
| --- | --- | --- | --- |
| normal_completion_01_pre | 关闭 | 关闭 | 同模块控制 |
| normal_completion_01_probe | CPU开启 | CPU开启 | WRITE、FADD、JFC及CPU控制区间 |
| normal_completion_01_post | 关闭 | 关闭 | 前后漂移和探针影响 |

三项均为paced：每次调用前后同步、5预热、10计时丢首值后9条。此节奏用于请求关联和探针验收，**不能将其时延直接与连续batch约7.5ms比较**。前后控制与probe使用同一模块、负载和节奏。本轮不再运行GPU trace或coverage。

成功应分别看到三次CASE_PASSED以及`NORMAL_COMPLETION_RUN_PASSED`。已有同名结果会拒绝覆盖；确需重跑两端使用同一新`--job`，但应先提供失败结果，不自动重复。

任一case失败即收集并停止后续项。中断后可用同一参数执行`--action collect`补收；只清理本任务进程组，不复位GPU/驱动、不杀其他任务。

## 5. 提供哪些结果

提供整个：

```text
test/normal_completion/results/normal_completion_01/
```

需要两端overlay/setup/request元数据和cases中的build/check/pre/probe/post日志；保留原始status、runtime、setup/preflight、api_samples、sample_summary、reference_control、fadd_path.jsonl、console及构建日志。不要只提供均值或截图，不需要.so/objects。

setup前置检查若在创建case前失败，保留终端错误和已经生成的request_node*.json。JFC布局错误在probe启动时发生，日志会自动收集；不要移除ABI检查。

结果齐全后开发侧离线执行，也可自行运行：

```bash
python3 test/normal_completion/analyze.py \
  --root test/normal_completion/results/normal_completion_01 \
  --output test/normal_completion/results/normal_completion_01/summary
```

输出目录须不存在。controls/decision先判断扰动；operation_summary、operations分别给WRITE/FADD界限及覆盖数；cpu_control_intervals/summary给CPU处理区间；REPORT提供简表。

## 6. 结果如何决定下一步

| 结果 | 收敛方向 |
| --- | --- |
| 目标CQE已有效、队首仍无效，且等待较长 | 直接支持CQ消费阻塞候选；再做无探针完成队列策略干预，验证完整Dispatch净收益 |
| CQE在队首已有效后，SDK仍迟交付 | 检查SDK调用内成本与轮询服务间隔，避免继续归因纯远端atomic |
| 直到很晚仍观测到目标CQE无效 | 继续查完成可读之前的设备/顺序/传输/内存访问路径；只在这时优先补匹配双向WRITE负载 |
| CPU QUIET处理区间很小 | 降低“CPU阻塞排空解释主要差额”的优先级；不等于GPU发布/等待QUIET成本已测全 |
| CPU QUIET处理区间长 | 核对具体调用/Proxy与API边界，再设计保留正确性保证的干预，不能直接删QUIET |
| 目标始终未捕获有效瞬间、记录缺失或控制不通过 | 明确保留未知/带探针现象，先分析已有记录，不自动追加整套矩阵 |

门槛沿用两端及逐次max指标的CV≤5%、前后漂移和probe影响各≤5%。不删异常样本、不放宽门槛；通过也不代表所有微秒级差异均准确。所有时戳在各自本端CPU，不能跨机相减。操作/线程之间的等待重叠，不能相加为完整Dispatch，也不能把这轮URMA内部观察当作新的URMA/RDMA差距。
