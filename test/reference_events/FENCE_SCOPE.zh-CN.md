# Normal下一轮：缩小fence放宽范围，A-B-C-C-B-A关闭采集实验

**本页 A/B/C 暂缓，作为已测 fence 收益的备选验证。**已有两套后端对照中，202 的 URMA BF16 Dispatch 分别约 8.081 和 8.114 ms，verbs 约 7.216 和 7.149 ms。追平需要减少约 0.866–0.965 ms，即对应 URMA 基线的约 10.7%–11.9%。之前的 fence 干预约节省 0.278 ms，量级约为历史差额的三分之一。两项来自不同轮次，不直接相减作为剩余差额。本页只测放宽范围，当前窗口优先保留给主要瓶颈验证。[后端对照一](../fadd_path/results/fadd_path_01/BACKEND_COMPARISON.zh-CN.md) · [后端对照二](results/BACKEND_MATCHED_ANALYSIS.zh-CN.md) · [fence 干预数据](results/normal_progress_20261116/README.zh-CN.md)

已收到 `urma_fence_scope_01` 的完整结果。只关 204 tail fence 两次均测得约 7.82 ms，全开基线轮间相差 0.824 ms，按已定门槛保留 C 为候选。本页运行步骤保留作复现记录。[实测分析](results/urma_fence_scope_01_ANALYSIS.zh-CN.md)

现有数据优先指向从开始传输到接收侧最后一个远端 token 可消费的区间。202 上该段中位约 6.428→7.110 ms，增加约 0.682 ms。历史 204 的第 6 块 WRITE 从提交到完成观测，中位约 2.224→3.492 ms。另一次 204 密集采样发现 FADD 返回值可读后，仍反复空轮询约 0.285 ms。后两项是发送侧局部观测，可能并行或重叠，不能直接加到 202 的 Dispatch 差值。先依托已有日志和与实机匹配的 provider/驱动版本，核对通知完成交付、后续命令推进与接收端尾部的关系。新的实机 A/B 需要先验证改动覆盖到了主要等待，并以完整 BF16 Dispatch 为收益指标。[同批交替 A/B](results/urma_normal_tail_order_01_ANALYSIS.zh-CN.md) · [204 完成采样](../fadd_path/results/fadd_dense_01/ANALYSIS.zh-CN.md)

## 目的与依据

已有严格ABBA证明：只关闭204全部Normal数据FADD的fence，BF16 Dispatch两组改善3.35%/3.52%，平均约278μs。它包含tail通知、head反馈以及Combine相关数据请求，尚未确定是哪类约束贡献收益。阶段观察器在204引入6.34%扰动后已停止，不再采集GPU/CPU阶段或主机时钟。

本轮只回答：**仅关闭tail通知的fence、保留head反馈的fence，是否能保留已见收益？**先只测URMA，不切RDMA内核；候选稳定后，再用同口径RDMA基线做最终性能对比。

| 配置 | 202 | 204 tail通知 | 204 head反馈 |
| --- | --- | --- | --- |
| A all-on | 全部on | on | on |
| B peer-off | 全部on | off | off |
| C peer-tail-off | 全部on | off | **on** |

FADD始终STRONG、WRITE始终RELAX，窗口8、completion all、lane-mixed。barrier/recovery marker保持原fence，LL不参与。默认行为不变，不自动采用实验设置。

## 实现与边界

Normal GPU原有5处FADD调用显式标注2处Tail、3处Head，覆盖Dispatch与Combine。角色通过本地TransferCmd中ATOMIC原本未使用的atomic_offset字段传递，不改变命令结构大小、网络协议、远端地址或增量；不会用请求值、地址或ring编号猜角色。LL不设置该标记，verbs消费该ATOMIC时不使用此字段。

transport按角色设置SDK fence，C只放宽已标Tail的请求；Head及未知角色保留fence。每个transport对每类角色首次准备SDK WR时记录一次URMA_FADD_ROLE，不逐请求打印，不读取时钟。驱动要求Tail/Head均实际出现、fence与配置一致且无Unknown，才能接受结果。

**优先复用刚跑完 `urma_normal_tail_order_01` 的构建。** 该构建包含 `normal_atomic_role_version=1`，当前两个试验共用参考事件模块。校验会核对扩展 SHA、当前源码、构建参数和实际角色 fence。A/B/C 必须在各自节点使用同一个合格模块。若已有模块校验通过，构建入口会报告 `BUILD_ALREADY_PASSED`。若源码或构建参数改变，使用新构建目录。旧 `urma_fadd_fence_v1` 缺少本轮所需角色能力。

## 1. 两端环境

两端均为6.6.0-gdr_f00955680_diag，且先协调GPU0/udmac0d1e2可用。202用RANK=0，204用1。保持已成功的额外MACA运行库设置；不清理他人进程/共享内存，不修改PCI状态：

```bash
cd /home/h00918771/uccl-ep-xpu-integration-new
set -euo pipefail
export URMA_DIAG_HOME=/home/h00918771
export URMA_DIAG_PYTHON=/home/h00918771/metax_dev/venv-maca-py312/bin/python
export MACA_PATH=/opt/maca-3.9.0
export URMA_DIAG_LIBRARY_DIR=/home/h00918771/metax_dev/urma_after_kernel_20261029/diag/umdk_private_618957c3/lib
export VERBS_DIAG_LIBRARY_DIR="$URMA_DIAG_LIBRARY_DIR"
export URMA_DIAG_INCLUDE_DIR=/home/h00918771/metax_dev/urma_sdk_private_include
RANK=0  # 204改为1
BUILD="$URMA_DIAG_HOME/metax_dev/reference_event_build/urma_tail_order_v1"
ROOT=/home/h00918771/metax_dev/reference_event_results
JOB=urma_fence_scope_01
DEST="$PWD/test/reference_events/results"
CONSOLE="/home/h00918771/metax_dev/fence_scope_console/$JOB/node$RANK"
mkdir -p "$CONSOLE"
uname -r
mx-smi
```

## 2. 两端确认构建可复用

```bash
bash test/reference_events/run.sh --backend urma --action build --step trace \
  --node-rank "$RANK" --build-dir "$BUILD" --job "${JOB}_build" --output "$ROOT" \
  2>&1 | tee "$CONSOLE/build.log"
```

`--step trace` 选择支持 paced 计时的构建，正式对照的 GPU/CPU 事件采集和时钟线程关闭。成功标志为 `BUILD_PASSED` 或 `BUILD_ALREADY_PASSED`。若确认旧构建因源码或参数变化无法复用，换用 `urma_fence_scope_v1` 等新目录。其他构建错误保留 build.log、host_tests.log、manifest/request 和 precision_probe 日志。

## 3. 两端128-token正确性门禁

两端执行，202先启动、180秒内204启动，33480/33481空闲：

```bash
bash test/reference_events/run_fence_scope.sh --part smoke \
  --node-rank "$RANK" --master-addr 141.61.17.202 --port 33480 \
  --build-dir "$BUILD" --job "$JOB" --output "$ROOT" --destination "$DEST" \
  2>&1 | tee "$CONSOLE/smoke.log"
```

顺序smoke_all（B）和smoke_tail（C），各128 tokens、1预热/3采样。只用于真实Normal正确性、分类和设置门禁，不据此判断性能。

需两端两项CASE_END normal rc=0、checks全true，特别normal_atomic_roles=true。最后FENCE_SCOPE_PASSED part=smoke。runtime能力normal_atomic_role_version=1；202均on，204的B为off、C为tail-off。bench.log中SDK首类日志应为：

| 端/配置 | role=tail | role=head |
| --- | --- | --- |
| 202所有配置 | fence=on | fence=on |
| 204 A | fence=on | fence=on |
| 204 B | fence=off | fence=off |
| 204 C | fence=off | fence=on |

全部order=strong；不能出现role=unknown。旧模块、无分类能力或实际标志不符会拒绝。两端门禁均通过再继续。

## 4. 正式六项性能任务

```bash
bash test/reference_events/run_fence_scope.sh --part performance \
  --node-rank "$RANK" --master-addr 141.61.17.202 --port 33480 \
  --build-dir "$BUILD" --job "$JOB" --output "$ROOT" --destination "$DEST" \
  2>&1 | tee "$CONSOLE/performance.log"
```

顺序固定：r01_all_on → r01_peer_off → r01_peer_tail_off → r02_peer_tail_off → r02_peer_off → r02_all_on。脚本先复核两项smoke的正确性、角色能力、生效设置、模块SHA和boot。每项check→run→收集，失败即停；不覆盖同名结果。

固定4096 tokens、hidden7168、experts256、topk8、SM24、Dispatch chunk32、GPU0。5预热/10采样/丢首值，每项9个有效样本。保留原程序完整正确性检查、FP8/BF16 Dispatch、layout、Combine12/16。GPU/CPU事件、计时线程均关闭；一次性角色日志不是阶段采集。

成功为各项CASE_END normal rc=0、checks全true，最后FENCE_SCOPE_PASSED part=performance。失败仅提供已有文件，不手工跳过检查继续下一配置。

## 5. 提供哪些日志

DEST下八个完整目录，两端node0/node1以及自动收集console/build元数据均保留：

```text
urma_fence_scope_01_smoke_all/
urma_fence_scope_01_smoke_tail/
urma_fence_scope_01_r01_all_on/
urma_fence_scope_01_r01_peer_off/
urma_fence_scope_01_r01_peer_tail_off/
urma_fence_scope_01_r02_peer_tail_off/
urma_fence_scope_01_r02_peer_off/
urma_fence_scope_01_r02_all_on/
```

另提供CONSOLE的build.log、smoke.log、performance.log。保留实际bench.log角色标志、setup/preflight/runtime/status、host/build manifest、api_samples/sample_summary/reference_control、kernel/GPU快照。无需.so/objects或旧实验日志，不只提供截图，不删除离群值。

两端齐全后可选离线汇总（无需GPU，输出目录必须为新目录）：

```bash
"$URMA_DIAG_PYTHON" tools/reference_events/summarize_fence_scope.py \
  --root "$DEST" --job "$JOB" --output "$DEST/${JOB}_summary"
```

输出逐端、逐样本配对、逐配对A/B/C均值及B/A、C/A、C/B差异、CV和身份记录。主指标是BF16每个对应样本max(node0,node1)的均值，不是两机全局makespan。A/B/C每配置只有两次顺序反转的运行，样本不是独立运行次数。

## 提前固定的判定

本轮意图保留约3.4%的有限收益，不把5%作为必须达到的收益目标。若两配对B/A及C/A均改善至少2%、C/B绝对差均<1%、相关CV均≤3%，支持C是保留收益的较小作用范围候选；不是统计等效性证明或可立即生产使用的结论。仍需扩大形状/复用正确性验证。

若B保留收益而C明显变慢，说明仅tail放宽不足，head反馈相关约束也可能参与收益；不能据此断言只由head硬件时延导致。若B本轮不再改善、方向不一致、CV超标或正确性失败，保留数据和默认on，不自动重跑或展开矩阵。FP8/Combine如有回退同样记录，不隐去副作用。

若 C 通过，在考虑默认采用前，补长时间缓冲复用和环回检查，覆盖 4096 tokens 和一种不同负载。核对 tail 更新后接收端的 payload/epoch、head 反馈和 quiet/barrier。现有 A/B/C 的常规正确性通过可作为下一道验证的起点。若 C 未保住收益，优先核对 B 与 C 的区别并继续定位完成交付，当前结果不足以支持把 head 的 fence 也默认关掉。[204 密集完成采样](../fadd_path/results/fadd_dense_01/ANALYSIS.zh-CN.md)

RDMA不参与本轮内部因果对照；历史RDMA只作背景参考。候选稳定后再在各自适用内核、相同程序与计时口径下做最终比较。

## 本地验证限制

真实transport主机模拟检查Tail/Head/Unknown在on/off/tail-off下的SDK标志、LL/barrier不变；源检查确认5处角色对应的tail/head地址，剥除角色元数据后原kernel算法语句保持；流程测试检查两门禁、ABC-CBA顺序和失败停止，汇总测试检查三配置身份和角色能力拒绝。开发机没有MetaX GPU，实际MACA编译及GPU正确性必须由服务器构建和门禁验证。
