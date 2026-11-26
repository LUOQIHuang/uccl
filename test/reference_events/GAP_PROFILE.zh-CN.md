# 下一项主实验：补齐可信的优化后URMA/verbs阶段差额

**当前暂停本页的运行计划。** 已有大区间证据足够选定下一处细拆。先看用原始记录得到的[具体FIFO阻塞请求与FADD完成小阶段](NORMAL_BLOCKER_REVIEW.zh-CN.md)。以下保留原计划供查阅。

本轮不是优化参数扫描。目标是先回答“完整Normal Dispatch的额外时间主要落在哪个时间区间”，再安排那个区间内的干预。单纯观察某条WRITE/FADD很慢，不能替代这份对照。入口为[run_gap_profile.sh](run_gap_profile.sh)，分析为[summarize_gpu_gap.py](../../tools/reference_events/summarize_gpu_gap.py)。

## 1. 现在确实缺什么

| 缺口 | 已有数据与限制 | 本轮如何补、必要性 |
| --- | --- | --- |
| 优化后URMA的可代表正常执行的完整GPU阶段记录 | 202c1/204c0、tail-off的完整trace中，202 BF16最大CV=10.0887%；204采集后慢5.7701%，两端representative均False | 保留原12个GPU点，关闭全部逐请求CPU trace及heartbeat，减少观测干扰。原来那份不能用来量化正常性能差额 |
| 同定义的verbs 12点记录 | 已归档verbs_ref_4096_matched_on_01是早期3点，没有本轮boundaries能力元数据，不能凭空补成12点 | verbs在允许的内核窗口按同一入口、GPU点定义及采样计划补齐；当前不要求切核 |
| 观测节奏是否保留连续benchmark的后端差距 | 历史batch/paced会移动两端本地等待，204局部曾明显变化 | 两端各保留batch前后控制，跨后端同时比较batch与paced差额；不只凭paced内部图外推原benchmark |
| 上轮完成可读实验的来源元数据 | normal_completion仍缺42条必需路径，原始目标请求可复算，但完整来源验收未完成 | 补交原有文件，不重跑GPU，不作为本轮启动前置条件 |

以上第一项来自[既有api_controls.csv](results/urma_device_pair_01/urma_c1_c0_01_summary/api_controls.csv)。它是已经观测到的失败原因，不是为了增加新测试而假设的问题。关闭CPU trace能否充分消除干扰仍需要本轮控制验证，不能提前承诺通过。

昨天的writes-first：干预覆盖通过，完整BF16变化+0.033%/−0.124%，已经关闭。纯WRITE能力微测只能筛选带宽余量，不能单独确定Normal的性能回退根因，本轮不发布它作为主实验。扩窗、拆链、CQE数量和新fence矩阵都不在本轮范围内。

## 2. 保留哪些GPU点、实际能判断什么

不增加GPU打点，不修改C++、kernel、fence、队列和完成策略。仅在Python采集控制中停止启动CPU timeline；仍启用原GPU参考时钟和12点检查。

```text
本端发送角色：首token准备(0) → 首WRITE算子调用前(1)
                  → 第6块就绪/发布(10/11)
                  → 末块就绪/发布(3/4)

本端接收角色：最后tail检查尝试/观察成功(5/6)
                  → 最后epoch检查前/最后入站token有效(7/2)
                  → Forwarder结束(8) → Receiver结束(9)
```

发送和接收并行；本端发往远端的末WRITE，与本端收到的末token不是同一个方向、同一个请求。不能把它们相减称作某条WRITE的网络传输时延。

主输出先将**同一主机的一条时间轴包络**按实际先后分成：

1. 首token标记→首WRITE标记。
2. 首WRITE标记→该主机最后入站token有效。
3. 最后入站token有效→该主机Receiver最后结束。

三段可以按单次记录对账为这条包络，**不代表三个独立、可相加的算子成本或完整因果关键路径**。API减包络的有符号余量另列，不强行命名成某个阶段，也不截成0。末块就绪/发布、tail/epoch、Forwarder等按角色/通道另行细化。最后一次tail检查的耗时不是整个接收等待。

当前URMA记录只能描述URMA自身，待verbs记录通过相同控制后才能计算对应区间差额。两端时间戳绝不跨机器直接相减。GPU时钟为采样RAW主机时钟，保留clock_stats；小于观测时钟间隙量级的差异不作为瓶颈证据。

## 3. 目前只有URMA时，直接这样运行

两端同步本分支到同一提交，进入实际仓库：

```bash
cd /home/h00918771/uccl-ep-xpu-integration-new
git rev-parse HEAD
uname -r
```

应为`6.6.0-gdr_f00955680_diag`。默认复用刚刚成功的：

```text
BUILD  /home/h00918771/metax_dev/writes_first_batch_build/urma_v1
Python /home/h00918771/metax_dev/venv-maca-py312/bin/python
MACA   /opt/maca-3.9.0
UMDK   /home/h00918771/metax_dev/urma_after_kernel_20261029/diag/umdk_private_618957c3/lib
202    udmac1d1e2，enp40s0
204    udmac0d1e2，enp34s0f5u1u4
GPU    GPU0，0000:21:00.0，上游atomic端口auto
```

**URMA不重新编译。**本次没有修改生产C++或GPU源文件，已成功模块有reference_boundary_version=1。脚本每项仍核验module与源指纹；如果报SOURCE_CHANGED_SINCE_BUILD，先提供当前HEAD和manifest，不跳过检查、不盲目启动新构建。若设备/接口/GPU实际变化，先使用实际值核对；本轮不更改驱动、PCIe或电源设置。

202启动：

```bash
bash test/reference_events/run_gap_profile.sh --backend urma --node-rank 0
```

204随后启动：

```bash
bash test/reference_events/run_gap_profile.sh --backend urma --node-rank 1
```

默认JOB=normal_gap_gpu_01，port33980；两端保持同一JOB/port。固定4096 tokens/rank、hidden7168、topk8、experts256、SM24、NVL/RDMA chunk4/32、5预热/10采样、丢首条保留9条。202全开数据fence、204仅tail-off；lane-mixed、窗口8、ordered、completion all，保持当前已验证基线。正确性检查保留。

自动运行以下五项，不需要另外跑smoke：本次没有新增native ABI/GPU点，仍执行已有扩展预检和Normal正确性检查。

| 次序 | 后缀 | 内容 | 为什么保留 |
| --- | --- | --- | --- |
| 1 | batch_pre | 原连续调用、无采集 | 锚定实际benchmark形态 |
| 2 | paced | 逐次同步、无采集 | 与trace同节奏的前控制 |
| 3 | trace | 仅GPU12点及采样时钟 | 获取本轮需要的阶段边界；没有CPU请求链/heartbeat |
| 4 | post | 与paced相同 | 检查采集影响及前后漂移 |
| 5 | batch_post | 与batch_pre相同 | 检查连续执行的复现和状态漂移 |

每项先check再run，单case超时600s；失败立即停止并自动收集已有结果。对端退出导致本端等待时，只中断本轮终端并保留日志，不杀其他进程。脚本拒绝覆盖已有node目录；故障修复后若确需新任务，显式使用新的--job及--destination，两端相同，不删除原结果。

输出：`/home/h00918771/metax_dev/normal_gap_gpu_results/`；自动归档：`test/reference_events/results/normal_gap_gpu_01/urma/`。

## 4. verbs窗口允许后，运行另一半

**现在只有URMA就先不执行本节，不要求为了本轮切核。**verbs应使用`6.6.0-145.3.34.165.oe2403sp3.aarch64`。旧3点模块不能当作本轮已构建版本；首次在verbs环境两端各新构建一次：

202：

```bash
bash test/reference_events/run_gap_profile.sh --backend verbs --action build --node-rank 0
```

204：

```bash
bash test/reference_events/run_gap_profile.sh --backend verbs --action build --node-rank 1
```

默认新目录`/home/h00918771/metax_dev/normal_gap_gpu_build/verbs_v1`，不覆盖旧模块。看到VERBS_BUILD_PASSED并收集成功，再在两端分别运行：

```bash
# 202：
bash test/reference_events/run_gap_profile.sh --backend verbs --node-rank 0
```

```bash
# 204：
bash test/reference_events/run_gap_profile.sh --backend verbs --node-rank 1
```

同样五项、同一GPU点和样本计划。verbs原有DMA-BUF、rocep40s0f0、GID3语义保留，不应用URMA tail-off。GID若实际验证值不同，必须两端一致指定--gid-index并记录，不把不同配置静默合并。MACA/Torch/GPU驱动应与本轮URMA一致，编译源文件指纹也要一致；分析器拒绝公共源码或GPU运行库改变后的强行匹配。

## 5. 交回哪些数据、怎样汇总

交回整个`test/reference_events/results/normal_gap_gpu_01/`中本次实际后端目录，包括五项两端完整内容。至少保留：

- setup、host、build/module manifest、runtime、status、benchmark副本、console/check/run及bench.log。
- api_samples、sample_summary、reference_control、reference_correctness、measurement_boundary、GPU版本和内核快照。
- trace中的event_trace.jsonl，包含全部GPU记录、counts及clock_stats。cpu必须为null、cpu_events必须为false；不是空造CPU时间。
- verbs新构建日志及manifest；URMA自动带回现有模块的构建记录即可，不需要.so/objects。

URMA两端到齐后可离线汇总，RDMA暂不可用也不影响先核验：

```bash
python3 tools/reference_events/summarize_gpu_gap.py summarize \
  --root test/reference_events/results/normal_gap_gpu_01 \
  --backend urma --output test/reference_events/results/normal_gap_gpu_01/summary_urma
```

verbs到齐后：

```bash
python3 tools/reference_events/summarize_gpu_gap.py summarize \
  --root test/reference_events/results/normal_gap_gpu_01 \
  --backend verbs --output test/reference_events/results/normal_gap_gpu_01/summary_verbs
```

两份通过后：

```bash
python3 tools/reference_events/summarize_gpu_gap.py compare \
  --urma test/reference_events/results/normal_gap_gpu_01/summary_urma \
  --verbs test/reference_events/results/normal_gap_gpu_01/summary_verbs \
  --output test/reference_events/results/normal_gap_gpu_01/comparison
```

这些输出目录必须尚不存在。开发端也会独立复算，不只看服务器summary。

旧normal_completion元数据可以顺便补收，两端分别执行以下命令，204改为1：

```bash
bash test/normal_completion/run.sh --action collect --node-rank 0 --job normal_completion_01
```

这只复制现有文件，不运行GPU；不阻塞本轮，也不为补归档重跑旧实验。

## 6. 预先规定结果如何改变下一步

先核对正确性、全部GPU点、身份和样本，再检查BF16：trace相对前后paced均值变化≤5%、前后paced漂移≤5%、各组CV≤5%；连续batch前后漂移≤3%、CV≤5%。旧门槛不放宽，不删去波动样本。失败保留原始观察，但不拿它量化正常性能回退，不原样追加重跑。

两后端通过后，还要比较batch与paced的后端差距；差异超过3个百分点，明确标记节奏不一致，不直接把paced阶段用于解释连续benchmark。

| 通过验收后的主要差额区间 | 下一步才研究什么 |
| --- | --- |
| 首token→首WRITE显著增加 | 数据准备、首次命令发布/启动；不先死磕末尾FADD |
| 首WRITE→最后入站有效增加 | 用末块就绪/发布及tail/epoch进一步定位发送推进还是接收可见，再接上既有CPU/CQ边界；不是把整段称作纯网络 |
| 最后入站有效→Receiver结束增加 | 聚焦转发、输出、反馈及结束依赖 |
| 主要差额仍在API未覆盖余量 | 先补入口/prepare/notify/退出边界，不把未知余量归给atomic |

同一包络内有并行流水，比较得到的是时间区间差额，不是“移除某算子必然回收多少”的证明。要称主要瓶颈，差额必须与整体差距同数量级、方向稳定，且显著大于观测时钟间隙；仅某个请求或某个百分位变长不够。对明确区间再选因果干预，干预必须同时改善未采集的完整Dispatch才接受优化。

本轮不能保证一次运行就定位设备内部根因，也不承诺消除剩余差距。它补的是当前缺失且决定排查方向的直接阶段对照；如果GPU-only仍明显扰动，这种观测方案就停止用于定量归因，不能再用参数变化反复消耗机时。
