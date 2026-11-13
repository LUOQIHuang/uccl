# 下一步：只验证204端Normal数据FADD的fence是否增加等待

本轮已完成：[结果分析](results/urma_fadd_fence_01_summary/ANALYSIS.zh-CN.md)。BF16两配对分别改善3.35%/3.52%，保留为部分候选收益，默认仍on；不需要重复下文已完成任务。

下一步使用[开关前后同位置稀疏观测运行文档](FADD_FENCE_OBSERVE.zh-CN.md)，验证已见收益是否对应通知推进/FIFO回收变化，不重跑本页ABBA。

## 本轮目的与现有数据依据

当前主目标仍是解释并减少Normal BF16的URMA-vs-verbs回退，不继续窗口扫描或增加密集GPU打点。

已有证据：

- 历史匹配paced的202 BF16：verbs 7.149ms、URMA 8.114ms，约+13.50%。
- 204 WRITE CQE→对应通知FADD CQE的中位间隔：verbs 4.18μs、历史URMA 659.75μs，本轮URMA 632.55μs。每组1188对，全都已在WRITE CQE前提交FADD。
- “提前提交”不等于同时提交：204历史WRITE post_end→FADD post_end中位数为verbs 9.095μs、URMA 357.805μs；本轮URMA356.600μs。FADD提交本身也晚，不能把整段约633μs全部解释成硬件atomic耗时。
- FIFO重放已看到，末块发布后到CPU读取的间隔中位数204为3.578ms，99%以上时间软件名额占满、队首主要为尚无CQE观测的ATOMIC。
- 204窗口8→4未得到BF16净收益，停止该扫描，保留8。
- 实现中URMA FADD为STRONG并保留fence；verbs Native FADD只显式置IBV_SEND_SIGNALED，没有同名IBV_SEND_FENCE设置。这是待验证差异，不表示两设备API语义可直接互换。

因此本轮只回答：**在仍保留STRONG事务顺序时，204端Normal数据FADD额外的fence约束，是否造成可消除的净性能损失？**

## 唯一干预和准确作用范围

| 计划 | 202 / node0 | 204 / node1 |
| --- | --- | --- |
| all-on：A | Normal数据FADD fence开启 | Normal数据FADD fence开启 |
| peer-off：B | 开启 | **关闭** |

只有这一位改变：FADD仍为STRONG，WRITE仍RELAX，窗口8、completion all、lane-mixed、epoch检查及GPU kernel、缓冲区和原始正确性检查均保持。barrier-kind FADD与故障恢复marker保留原fence，LL行为不变。默认配置仍为on，本测试不修改生产默认值。

**作用范围是204端所有Normal数据通道的ATOMIC请求，包括tail通知和数据反馈；也覆盖Normal Combine的数据原子请求。**本轮先看BF16 Dispatch净耗时，其他API保留为副作用检查。若B改善，首先支持的是这类FADD fence约束有代价，不能直接宣布只由某一条tail通知造成，也不能直接推广为其他设备/形状均可关闭。

fence与STRONG不同。公开API中fence涉及前序READ/ATOMIC依赖；保留STRONG是为了单独测试fence，不同时放宽两种顺序条件。当前私有SDK和硬件是否允许这种设置正确且有效，必须由本轮真实正确性和性能验证，不预设成功。

## 本轮任务量

每端构建一个新模块；128-token B配置验证一次；两端通过后，4096-token关闭采集ABBA四项。不增加LL、WRITE强弱序、混合提交、窗口或主动trace矩阵。

128-token用于新同步约束的正确性/配置门禁，不用来判断性能。4096的每项仍执行原始真实Normal正确性检查，再接受性能结果。任何失败即停止并收集，不自动采用旧模块或降级参数。

## 1. 两端设置环境

202用RANK=0，204改为1，其余参数相同。先拉取本轮代码。使用刚成功的URMA内核、私有库和已协调的GPU0：

```bash
cd /home/h00918771/uccl-ep-xpu-integration-new
set -euo pipefail
export URMA_DIAG_HOME=/home/h00918771
export URMA_DIAG_PYTHON=/home/h00918771/metax_dev/venv-maca-py312/bin/python
export MACA_PATH=/opt/maca-3.9.0
export URMA_DIAG_LIBRARY_DIR=/home/h00918771/metax_dev/urma_after_kernel_20261029/diag/umdk_private_618957c3/lib
export URMA_DIAG_INCLUDE_DIR=/home/h00918771/metax_dev/urma_sdk_private_include
# 额外运行库沿用此前成功设置，不混入旧MACA库。
RANK=0  # 204改为1
BUILD=/home/h00918771/metax_dev/reference_event_build/urma_fadd_fence_v1
ROOT=/home/h00918771/metax_dev/reference_event_results
JOB=urma_fadd_fence_01
DEST="$PWD/test/reference_events/results"
CONSOLE="/home/h00918771/metax_dev/fadd_fence_console/$JOB/node$RANK"
mkdir -p "$CONSOLE"
uname -r
mx-smi
```

预期内核6.6.0-gdr_f00955680_diag、设备udmac0d1e2、网络enp33s0、MACA3.9.0.29/KMD3.10.14。Atomic Request、runpm、私有库沿用已成功配置，脚本只检查，不切核或修改硬件。

不要手动export新的UCCL_URMA_NORMAL_FADD_FENCE。两端使用同一plan，由node-rank选择本地on/off，驱动会覆盖继承的实验环境变量，避免A被误设为off。

## 2. 两端各构建一次新模块

本轮修改的是CPU transport及能力查询，GPU kernel不增加打点。必须使用新的BUILD，旧boundaries模块没有该能力，不能复用.so或修改manifest。A/B全用这个新模块：

```bash
bash test/reference_events/run.sh --backend urma --action build --step trace \
  --node-rank "$RANK" --build-dir "$BUILD" --job "${JOB}_build" --output "$ROOT" \
  2>&1 | tee "$CONSOLE/build.log"
```

这里step trace只选择具备参考事件能力的构建；后续任务全部paced，GPU/CPU记录和主机时钟关闭。同一模块用于所有计划，避免构建差异混入A/B。

构建包含主机模拟和MetaX编译检查，不启动GPU benchmark。成功为BUILD_PASSED或BUILD_ALREADY_PASSED。失败提供CONSOLE/build.log及BUILD的build.log、host_tests.log、request/manifest、precision_probe日志并停止，不需要.so/objects。

## 3. 新设置的128-token验证

202先启动，180秒内204启动；33280/33281端口空闲：

```bash
bash test/reference_events/run_fadd_fence.sh --part smoke \
  --node-rank "$RANK" --master-addr 141.61.17.202 --port 33280 \
  --build-dir "$BUILD" --job "$JOB" --output "$ROOT" --destination "$DEST" \
  2>&1 | tee "$CONSOLE/smoke.log"
```

此项固定peer-off：202 on、204 off。预热1次/采样3次，保留原Normal正确性检查，采集关闭。必须看到：

- preflight/runtime中normal_fadd_fence_version=1、normal_max_inflight=8；202 normal_fadd_fence=on、204 off。
- 真实[URMA] mode=normal初始化日志含同样的normal_fadd_fence，fadd_order=strong、barrier_fence=per_request。
- CASE_END normal rc=0、全部checks=true，特别是normal_fadd_fence=true。
- 最后FADD_FENCE_SUITE_PASSED part=smoke。

脚本预检查会拒绝缺少能力或生效值错误的旧模块；运行还检查实际transport初始化日志，避免只验证了环境字符串。两端smoke均通过后再继续。smoke波动不作为性能判定。

## 4. 正式4096-token ABBA

两端执行同一条命令，仅RANK不同：

```bash
bash test/reference_events/run_fadd_fence.sh --part performance \
  --node-rank "$RANK" --master-addr 141.61.17.202 --port 33280 \
  --build-dir "$BUILD" --job "$JOB" --output "$ROOT" --destination "$DEST" \
  2>&1 | tee "$CONSOLE/performance.log"
```

脚本先检查本端smoke正确性、生效值、模块SHA和boot未变，再顺序运行：

| 后缀 | 202 FADD fence | 204 FADD fence | 采集 |
| --- | --- | --- | --- |
| r01_all_on | on | on | 关闭 |
| r01_peer_off | on | off | 关闭 |
| r02_peer_off | on | off | 关闭 |
| r02_all_on | on | on | 关闭 |

5次预热/10次采样/丢首值，9个有效样本。固定4096 tokens、hidden7168、experts256、topk8、SM24、Dispatch chunk32、NVL chunk4，每端一张GPU0。保留FP8/BF16 Dispatch、layout、Combine12/16五项API及正确性检查。

每项check→run→自动收集，失败收集已有文件后停止；不覆盖同名结果。每项CASE_END normal rc=0、checks全true，最后FADD_FENCE_SUITE_PASSED part=performance。若需要重跑，使用新JOB且先完成其smoke，不修改旧结果。

## 5. 提供哪些数据

DEST下五个完整目录，均包含node0/node1以及自动收集的console/build信息：

```text
urma_fadd_fence_01_smoke128/
urma_fadd_fence_01_r01_all_on/
urma_fadd_fence_01_r01_peer_off/
urma_fadd_fence_01_r02_peer_off/
urma_fadd_fence_01_r02_all_on/
```

另提供CONSOLE下build.log、smoke.log、performance.log。目录中保留api_samples、sample_summary、reference_control、status、bench.log、runtime、setup/preflight、host、manifest及GPU/kernel快照，不只提供均值截图，不删除离群点，不需要.so/objects。失败时提供当次已有文件即可。

两端齐全后可选离线汇总，不需GPU：

```bash
"$URMA_DIAG_PYTHON" tools/reference_events/summarize_fadd_fence.py \
  --root "$DEST" --job "$JOB" --output "$DEST/${JOB}_summary"
```

输出per_node、paired_calls、rounds、decision、context。工具复核源码、模块、boot、库及采样口径一致，只允许指定fence计划差异，不会把peer-off数据作为默认URMA-vs-verbs对照。

## 如何根据结果推进

主指标为BF16每个对应样本max(node0,node1)的均值，同时查看各端CV及均值；不是跨机器时钟的全局makespan。

| 结果 | 结论及动作 |
| --- | --- |
| 两配对B都改善≥5%，相关CV≤5%，所有正确性通过 | 支持204 Normal数据FADD的fence有净性能代价；再扩大正确性验证，并决定补短采集确认通知间隔/FIFO阻塞是否同步缩短，不自动修改默认配置 |
| 两配对变化绝对值均<3%且稳定 | 该fence开关不是主要净优化点，保留on，不立即改FADD RELAX或再展开多开关矩阵 |
| 变慢、方向不一致或CV超限 | 保留日志审查，不以局部CQE可能变短声称优化成功，不自动重跑 |
| 正确性失败、超时或完成错误 | 停止该计划，保留on；说明该环境下设置不可直接使用，不继续采性能 |

即使获得收益，也不能仅凭一次形状的正确性就认定所有Normal场景可关闭fence，更不能据此取消STRONG、epoch或barrier约束。下一步由这项单变量结果决定，不先并行开展其他实验。

## 本地验证范围

主机模拟会通过实际transport检查SDK WR中的fence/placement/CQE标志，验证默认、on/off、LL和barrier不变及非法组合拒绝；Python测试覆盖节点选择、旧能力拒绝、环境清理和汇总身份校验。开发机没有MetaX GPU，真实GPU编译和语义正确性仍由服务器构建及上述门禁验证。
