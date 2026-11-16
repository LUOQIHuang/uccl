# 下一阶段：一次有明确排除能力的Normal软件回收策略实验

## 总目标和本轮只回答的问题

总目标仍是解释并减少URMA Normal BF16相对verbs的回退。近期观察器引入不对称扰动，已经停止；不重复observe、历史取证或fence收益复测。tail/head三配置先暂缓，避免两个候选同时占用设备或混合开关。

本轮问题：**在保留全部网络顺序约束时，只提前回收已确认完成的普通请求的软件额度，是否能降低整体Dispatch时延？**这是对已见FIFO队首阻塞的直接干预，不是再补阶段图。

## 1. 为什么值得这一次测试（离线审查已完成）

审查proxy.cpp、rdma.cpp、urma_transport.cpp得到：

- 两版共享notify_gpu_completion的按序队首回收规则，不是URMA独有的软件逻辑。
- 普通WRITE/ATOMIC在CPU读取命令时，已经从GPU命令FIFO pop；fifo_pending_是后续等待网络完成的软件额度记账，不是仍未读取的GPU命令槽。
- 即便后续WRITE已观察到成功CQE，只要队首ATOMIC未完成，旧规则仍保留这些已完成WRITE的额度。
- URMA SDK每次最多读16条完成，但transport已循环排空完整批次；不能仅凭16与verbs批量大小不同，就臆测每16条都被迫绕一次Proxy循环。

利用此前已验收BF16原始请求记录离线重算“CPU观察CQE→pending条目回收”：

| 端/后端 | ATOMIC中位数 | 大WRITE P95 | 大WRITE等待>50μs的比例 |
| --- | ---: | ---: | ---: |
| 202 verbs | 0.17μs | 0.50μs | 0% |
| 202 URMA | 0.27μs | 603.27μs | 46.63% |
| 204 verbs | 0.17μs | 0.55μs | 0% |
| 204 URMA | 0.285μs | 431.40μs | 42.76% |

每端各后端2268条ATOMIC、1188条大WRITE，九个有效窗口。数据和源SHA见[离线审查结果](results/normal_retire_evidence_20261116/software_retire_wait.csv)及同目录sources.json，复现工具tools/reference_events/audit_retire_wait.py。它们来自历史带观察器记录，不能当作本次未采集程序的精确代价或独立重复次数。

结合此前204末块发布后约99.59%的区间表现为额度满且队首ATOMIC尚无完成观测，得到一个可证伪候选：**慢完成与按序记账叠加，阻止已完成WRITE释放额度，放大后续命令推进延迟。**这不证明CPU处理CQE本身很慢，也不证明回收这些名额就一定有净收益。

窗口8→4阴性实验没有检验本项：本轮不改窗口数值，而是改变已完成条目是否继续占名额。它可能提高有效并行度，收益不能被解读为硬件atomic变快。只改变204，是依据该端发送进度影响202总时延的已有观测；阴性结果只排除本端/负载上的这个候选，不排除所有回收策略。

## 2. 实验改动和正确性边界

| 项目 | A ordered | B peer-ready |
| --- | --- | --- |
| 202回收 | 按队首顺序 | 不变 |
| 204普通请求回收 | 队首未完成时保留后续已完成条目 | 仅回收已经观察到完成的普通条目 |
| 未完成请求 | 保留 | 保留，不伪造完成 |
| quiet/barrier | 原顺序 | 不越过控制分隔符，不在前序未完成时pop控制命令 |
| GPU命令发布、网络请求顺序/地址/增量 | 原路径 | 不变 |
| WRITE RELAX/FADD STRONG/fence开启 | 保留 | 保留 |
| 窗口数值/SDK完成策略 | 8 / all | 8 / all |

只在URMA Normal FIFO后端启用B；RDMA与LL仍走默认按序策略。普通请求释放字节额度必须有真实完成记录，保留SDK错误检查；不修改GPU kernel。默认仍ordered，所有任务同一个新模块，A/B共同承担同一实现结构的开销。

主机测试覆盖所有4请求完成排列、未完成请求不回收、字节记账、8个名额场景、quiet/barrier前序依赖，并编译执行从生产Proxy提取的真实回收函数。它们不能替代实际GPU/SDK正确性，实机必须保留原Normal完整正确性检查。

## 3. 固定任务预算与停止规则

一次新构建；一次128-token B正确性门禁；一次4096-token ABBA共四项，不增加其他形状或LL。只有关闭采集的整调用计时，不启动GPU/CPU事件、主机时钟或Profiler。

主要指标：BF16每个对应样本max(node0,node1)的均值，另核对各端CV和其他API副作用。它不是跨机器全局makespan。5次预热/10次采样/丢首值，每项9个有效值，两次反向配对，样本不是独立运行次数。

| 结果 | 判定与下一步 |
| --- | --- |
| 两配对B均改善≥5%，相关CV≤5%，正确性通过 | 支持该回收策略是有实质净价值的候选；再扩大复用/形状正确性验证，随后做同口径RDMA最终对照 |
| 两配对绝对变化均<3%，稳定 | 排除它作为本条件下主要净优化点；保持ordered，停止此开关，不换节点/窗口继续扫描 |
| 两配对均恶化≥5% | 放弃该策略，保留ordered；说明提前放行可能增加后端压力，不能仅凭完成回收更快声称优化 |
| 3%～5%或方向不一致、CV超标 | 保留有限结果，不自动增加轮次/打点，也不硬判根因 |
| 正确性失败/SDK错误/超时 | 停止并收集当前任务，保持默认，不启动下一项、不自动清理或重置设备 |

该轮不能证明或排除全部URMA底层差异，不把一个小收益包装成完整根因。两种内核和设备不同，历史RDMA只作背景；本轮不需要切核重测RDMA。

## 4. 两端环境设置

先拉取本轮提交，两端内核6.6.0-gdr_f00955680_diag，GPU0和URMA设备已协调无人占用。202设RANK=0，204为1，保持此前成功的MACA/私有库/额外运行库配置：

```bash
cd /home/h00918771/uccl-ep-xpu-integration-new
set -euo pipefail
export URMA_DIAG_HOME=/home/h00918771
export URMA_DIAG_PYTHON=/home/h00918771/metax_dev/venv-maca-py312/bin/python
export MACA_PATH=/opt/maca-3.9.0
export URMA_DIAG_LIBRARY_DIR=/home/h00918771/metax_dev/urma_after_kernel_20261029/diag/umdk_private_618957c3/lib
export URMA_DIAG_INCLUDE_DIR=/home/h00918771/metax_dev/urma_sdk_private_include
RANK=0  # 204改为1
BUILD=/home/h00918771/metax_dev/reference_event_build/urma_retire_v1
ROOT=/home/h00918771/metax_dev/reference_event_results
JOB=urma_retire_01
DEST="$PWD/test/reference_events/results"
CONSOLE="/home/h00918771/metax_dev/retire_console/$JOB/node$RANK"
mkdir -p "$CONSOLE"
uname -r
mx-smi
```

不要手动设置UCCL_URMA_NORMAL_RETIRE或fence，驱动会为每项覆盖继承的实验变量。脚本检查设备/barrier/硬件状态，不清理他人进程，不切核/修改PCI配置。

## 5. 两端各构建一次新模块

旧fadd_fence_v1/fence_scope_v1模块均不能复用；本轮修改CPU Proxy，需新BUILD：

```bash
bash test/reference_events/run.sh --backend urma --action build --step trace \
  --node-rank "$RANK" --build-dir "$BUILD" --job "${JOB}_build" --output "$ROOT" \
  2>&1 | tee "$CONSOLE/build.log"
```

step trace只选择现有paced协议所需的编译能力，后续采集关闭。成功BUILD_PASSED/BUILD_ALREADY_PASSED；构建失败停止，保留build.log、host_tests.log、manifest/request与precision_probe。不修改manifest或复用另一端.so。

## 6. 128-token正确性门禁

两端执行同一条命令，仅RANK不同。202先启动、180秒内204启动；33580/33581端口空闲：

```bash
bash test/reference_events/run_retire_ab.sh --part smoke \
  --node-rank "$RANK" --master-addr 141.61.17.202 --port 33580 \
  --build-dir "$BUILD" --job "$JOB" --output "$ROOT" --destination "$DEST" \
  2>&1 | tee "$CONSOLE/smoke.log"
```

此项B：202 ordered、204 ready，128 tokens/1预热/3采样，保留原Normal正确性检查。两端均需CASE_END normal rc=0、checks全true，特别normal_fifo_retire=true；最后RETIRE_AB_PASSED part=smoke。

runtime中normal_fifo_retire_version=1、normal_fifo_retire=ordered/ready；真实Proxy日志NORMAL_FIFO_RETIRE policy=ordered/ready limit=8。两端FADD fence仍on。旧模块、非FIFO、能力或设置不符会拒绝，不能降级继续。

## 7. 四项正式性能测试

两端smoke通过后：

```bash
bash test/reference_events/run_retire_ab.sh --part performance \
  --node-rank "$RANK" --master-addr 141.61.17.202 --port 33580 \
  --build-dir "$BUILD" --job "$JOB" --output "$ROOT" --destination "$DEST" \
  2>&1 | tee "$CONSOLE/performance.log"
```

顺序r01_ordered→r01_ready→r02_ready→r02_ordered。固定4096 tokens、hidden7168、experts256、topk8、SM24、Dispatch chunk32，每端GPU0。FP8、layout和Combine12/16作为同程序副作用检查保留，但不新增LL。

脚本检查smoke模块SHA/boot/策略未变，每项check→run→自动收集，失败即停，不覆盖结果。最终RETIRE_AB_PASSED part=performance。失败时提供已有文件即可，不为补齐目录重测。若需要另一次授权重跑，使用新JOB并重新通过其smoke，不修改旧数据。

## 8. 提供的日志（不需要传输操作说明）

DEST下五个完整目录，两端node0/node1及console/build元数据：

```text
urma_retire_01_smoke128/
urma_retire_01_r01_ordered/
urma_retire_01_r01_ready/
urma_retire_01_r02_ready/
urma_retire_01_r02_ordered/
```

另保留CONSOLE的build.log、smoke.log、performance.log。原始bench、api_samples/sample_summary/reference_control、status/runtime/setup/preflight、host/module manifest及kernel/GPU快照不可删。无需.so/objects，不只提供均值截图，不删除离群点。失败重点保留preflight_error、bench首发错误、kernel_before/after。

两端齐全后可选离线汇总（无需GPU；输出目录须不存在）：

```bash
"$URMA_DIAG_PYTHON" tools/reference_events/summarize_retire.py \
  --root "$DEST" --job "$JOB" --output "$DEST/${JOB}_summary"
```

工具复核除回收策略外同源码/模块/boot/库、fence开启、窗口8、采样口径、实际策略日志及两端配对，输出per_node、paired_calls、rounds、decision、context。decision是预定工程规则，不是统计显著性检验，不自动修改默认。

## 本阶段完成标准

用一次有限ABBA判断这个软件回收放大器能否带来真实净收益；阳性进入正确性扩大验证与RDMA同口径验收，阴性关闭该分支。任何结果都不恢复阶段观察器，不机械补历史日志，不同时开启tail/head、窗口或提交顺序矩阵。
