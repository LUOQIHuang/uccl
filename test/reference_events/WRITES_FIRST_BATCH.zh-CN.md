# Normal下一轮：验证WRITE/FADD分阶段提交是否有整体收益

**2026-11-26：本轮已完成，不再运行。**[独立复算分析](results/urma_writes_first_batch_01/ANALYSIS.zh-CN.md)：BF16两配对变化+0.033%/−0.124%，提交覆盖通过、整体无收益，保留lane-mixed。下文保留操作和预先判据以便核对，不追加同组重测。

当前仅需要URMA环境。入口：[run_writes_first_batch.sh](run_writes_first_batch.sh)。本轮只做四项A→B→B→A，聚焦4096-token BF16 Dispatch；不重跑扩窗、旧CQ探针、LL、tokens扫描或fence矩阵。

## 1. 测什么、为什么现在测

[完成可读性结果](../normal_completion/results/normal_completion_01/ANALYSIS.zh-CN.md)显示，选中204慢WRITE提交至SDK交付约2794μs，但CQE已经可读后到SDK交付的上界中位数只有4.44μs、最大7.78μs。CPU QUIET处理也只有微秒量级。下一步应干预CQE可读之前的路径，而非继续调整已经完成之后的轮询。

| 项目 | A：lane-mixed | B：writes-first |
| --- | --- | --- |
| Proxy本轮可提交命令 | WRITE及独立FADD，原样收集 | 相同 |
| SDK提交组织 | 原有每通道WRITE/FADD混合链 | 先flush各通道WRITE阶段，再提交独立FADD阶段 |
| 同通道WRITE→独立FADD顺序 | 保留 | 保留 |
| 请求携带的WRITE+通知配对 | 保留 | 保留，不拆开显式配对 |
| GPU kernel、数据、地址、请求条数 | 相同 | 相同 |
| 202/204 fence | 202全开、204仅tail-off | 相同 |
| WRITE/FADD顺序属性 | RELAX / STRONG | 相同 |
| 窗口、回收、完成策略 | 8、ordered、all | 相同 |

B只在**每个Proxy一次收集到的这批命令内**先提交WRITE，再提交独立FADD，不等待整个Dispatch的全部token发完。它对齐的是verbs Proxy的“先WRITE阶段、后独立FADD阶段”组织，不代表URMA所有硬件语义与verbs相同。这也不同于此前无收益的tail-interleaved：本轮改变SDK调用边界及跨通道阶段先后，不是仅在同一链中把tail移到对应WRITE后。

这是待验证假设，不预设收益。主判据直接用完整Dispatch。额外一次CPU采集只验证改动有没有生效，不把采集时的延迟计入性能结果。此前混合链/纯链的分组统计存在负载差异，不能替代这次A/B。

## 2. 环境及构建

固定内核`6.6.0-gdr_f00955680_diag`、MACA3.9、每端GPU0。202使用udmac1d1e2，204使用udmac0d1e2；GPU PCI默认0000:21:00.0，父端口auto。脚本不切内核，不修改PCI、驱动或设备电源设置。

两端先同步本分支，使`git rev-parse HEAD`一致，然后进入仓库：

```bash
cd /home/h00918771/uccl-ep-xpu-integration-new
git rev-parse HEAD
uname -r
```

默认路径如下；与目前成功环境一致，无需另拷贝module：

```text
Python   /home/h00918771/metax_dev/venv-maca-py312/bin/python
MACA     /opt/maca-3.9.0
UMDK     /home/h00918771/metax_dev/urma_after_kernel_20261029/diag/umdk_private_618957c3/lib
SDK      /home/h00918771/metax_dev/urma_sdk_private_include
新BUILD  /home/h00918771/metax_dev/writes_first_batch_build/urma_v1
输出ROOT /home/h00918771/metax_dev/writes_first_batch_results
归档DEST 仓库/test/reference_events/results/urma_writes_first_batch_01
```

**需要一次新构建。**旧代码构造函数禁止tail-off+writes-first组合，本次只放开这个有明确范围的配置组合，并增加主机WR检查；没有改GPU kernel和请求flag逻辑。保留之前normal_completion、normal_scope及credit的module，不覆盖它们。两端各构建自己的扩展，不跨机器复制.so。

202执行：

```bash
bash test/reference_events/run_writes_first_batch.sh \
  --action build --node-rank 0
```

204执行：

```bash
bash test/reference_events/run_writes_first_batch.sh \
  --action build --node-rank 1
```

两端构建可独立启动。构建入口使用1个编译任务，自动归档构建/host_tests/precision probe日志。看到BUILD_PASSED和收集成功再进入运行阶段；构建失败不要复用旧.so、修改manifest或跳过检查，只交回已收集的构建记录。

若实际私有SDK路径不同，仅在两端构建前设置正确变量；不要改为系统SDK：

```bash
# 仅在默认目录与实际不符时设置：
# export URMA_DIAG_INCLUDE_DIR=/实际的私有SDK/include
# export URMA_DIAG_LIBRARY_DIR=/实际的私有UMDK/lib
```

## 3. 两端运行四项对照

两端构建完成后，在两个终端分别运行，使用同一job、port及测试版本。先启动一端等待，随后及时启动另一端即可。

202：

```bash
bash test/reference_events/run_writes_first_batch.sh \
  --action run --node-rank 0 --master-addr 141.61.17.202
```

204：

```bash
bash test/reference_events/run_writes_first_batch.sh \
  --action run --node-rank 1 --master-addr 141.61.17.202
```

固定4096 tokens/rank、hidden7168、experts256、topk8、SM24、NVL chunk4/RDMA chunk32；每项5次预热、10次连续API计时、按计划丢首条保留9条。主指标为同序号两端本地CUDA事件耗时取max后求均值，不是统一跨机时钟的makespan。

自动执行顺序：

| 顺序 | job后缀 | 提交方式 |
| --- | --- | --- |
| A1 | r01_lane_mixed | 原方式 |
| B1 | r01_writes_first | WRITE阶段先flush |
| B2 | r02_writes_first | 同B1 |
| A2 | r02_lane_mixed | 回测原方式 |

每项先本地check再运行，验证module源码指纹、设备、实际fence/窗口/提交配置；运行包含原Normal正确性检查。保留现有FP8/Layout/Combine计时输出作为控制记录，但主决策只看BF16 Dispatch，不把有历史数量级问题的Combine加进本轮收益。

**计时时不开GPU或CPU事件采集。**每项BF16计时和summary写完之后，额外执行1次相同Dispatch，仅打开CPU请求记录，生成submit_witness.json。它核验SDK链中WRITE/FADD是否混合，不用于任何延迟推断。CPU采集失败会使该项验证失败，不会悄悄把采集时间混入9条均值。

B要求有效payload WRITE与独立FADD不再混在同一个SDK链。A每端每轮至少10%的payload字节需处在混合链，保证本轮有可干预的提交组合；额外单次采集会扰动分组，只作为覆盖证据，不能代表无采集时的精确比例。

脚本每项成功或失败都会自动收集现有文件。失败即停，默认每case超时600s；另一端若因对端退出而等待，可在本轮终端Ctrl-C，保留双方日志，不杀其他人的进程。结果目录存在时拒绝覆盖；需要故障修复后重测，给两端相同的新job和新destination，不删除原结果。

## 4. 汇总与固定判定

两端记录齐全后在任一有Python3的机器执行：

```bash
python3 tools/reference_events/summarize_submit.py \
  --root test/reference_events/results/urma_writes_first_batch_01 \
  --job urma_writes_first_batch_01 --cadence batch \
  --output test/reference_events/results/urma_writes_first_batch_01/summary
```

summary目录必须尚不存在。工具检查双方来源、原始样本配对、运行状态及提交覆盖，再生成per_node/paired_calls/rounds/decision/submission_coverage.csv。

| 结果 | 解释和后续 |
| --- | --- |
| 两配对BF16均减少≥5%，BF16各组CV≤5%，A和B各自轮间漂移≤3%，覆盖通过 | 支持提交组织有实质影响，之后才做匹配阶段复核及优化候选验证；不直接宣布追平RDMA |
| 两配对绝对变化均<3%，稳定且覆盖通过 | 关闭当前分阶段提交方向，保留原方式，不扫更多间隔/批次参数 |
| 两配对均增加≥5%且稳定 | 确认此策略负收益，保留原方式 |
| 波动、漂移超限、方向不一致、3%～5%边界、覆盖不足 | 不接受优化；保留原方式，先审查具体失败项，不自动追加重测 |

本次若有效，能回答“提交组织是否影响完整Dispatch、能改善多少”，仍不能独立拆出设备内部排队、远端访问和CQE生成各自时间。本次若无效，也能关闭一个实际实现差异，下一步再围绕CQE可读前的设备/GPU访问路径选择干预，不重复后置CPU轮询测试。

## 5. 需要交回哪些记录

交回自动收集的整个`test/reference_events/results/urma_writes_first_batch_01/`，包含build任务和四项两端记录，不只交summary：

- 每端构建console、build.log、host_tests.log、manifest及precision probe结果。
- 每case两端setup/host/build_manifest、runtime/status、bench/check/run日志。
- api_samples.jsonl、sample_summary.jsonl、reference_control.jsonl及保留的benchmark副本。
- submit_witness.json、submit_witness_summary.json；失败时也保留submit_witness_error.txt和console。
- 汇总成功后生成的summary完整目录。

另请补齐上一轮被.gitignore漏掉的**现有**normal_completion元数据。新提交已修复忽略规则，已有归档目录内的JSON/CSV/LOG会被正常识别。无需重跑GPU、无需重做setup。如果原始采集有文件尚未归档，可两端分别补收：

```bash
# 202执行；204把0改为1。collect只复制已有记录，不运行benchmark。
bash test/normal_completion/run.sh \
  --action collect --node-rank 0 --job normal_completion_01
```

交回`test/normal_completion/results/normal_completion_01/`中新出现的overlay/setup/request、各case的JSON/LOG、build记录和原始CSV即可。缺失必需路径列表见[raw_review/context.json](../normal_completion/results/normal_completion_01/raw_review/context.json)。补齐后由分析端运行原严格分析器；不要为缺失归档消耗GPU机时。

## 6. 本地已验证的范围

本地完成transport主机模拟测试（参考SDK头文件fixture）、新增配置组合的实际WR flag检查、脚本两端ABBA/失败即收集/不覆盖行为、计时后额外采集边界和汇总覆盖/漂移/阈值检查。私有UMDK ABI、C500运行正确性和收益仍由上述服务器构建与四项运行检验，不把CPU模拟当成GPU已通过。
