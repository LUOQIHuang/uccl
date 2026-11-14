# Normal下一轮：缩小fence放宽范围，A-B-C-C-B-A关闭采集实验

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

**必须新构建。**本轮增加GPU命令元数据和transport行为，旧urma_fadd_fence_v1不能复用。A/B/C必须共用各自本端同一新模块；新能力normal_atomic_role_version=1，脚本要求A/B也具备，避免拿旧A/B与新C混比。

## 1. 两端环境

两端均为6.6.0-gdr_f00955680_diag，且先协调GPU0/udmac0d1e2可用。202用RANK=0，204用1。保持已成功的额外MACA运行库设置；不清理他人进程/共享内存，不修改PCI状态：

```bash
cd /home/h00918771/uccl-ep-xpu-integration-new
set -euo pipefail
export URMA_DIAG_HOME=/home/h00918771
export URMA_DIAG_PYTHON=/home/h00918771/metax_dev/venv-maca-py312/bin/python
export MACA_PATH=/opt/maca-3.9.0
export URMA_DIAG_LIBRARY_DIR=/home/h00918771/metax_dev/urma_after_kernel_20261029/diag/umdk_private_618957c3/lib
export URMA_DIAG_INCLUDE_DIR=/home/h00918771/metax_dev/urma_sdk_private_include
RANK=0  # 204改为1
BUILD=/home/h00918771/metax_dev/reference_event_build/urma_fence_scope_v1
ROOT=/home/h00918771/metax_dev/reference_event_results
JOB=urma_fence_scope_01
DEST="$PWD/test/reference_events/results"
CONSOLE="/home/h00918771/metax_dev/fence_scope_console/$JOB/node$RANK"
mkdir -p "$CONSOLE"
uname -r
mx-smi
```

## 2. 两端各构建一次

```bash
bash test/reference_events/run.sh --backend urma --action build --step trace \
  --node-rank "$RANK" --build-dir "$BUILD" --job "${JOB}_build" --output "$ROOT" \
  2>&1 | tee "$CONSOLE/build.log"
```

step trace只选择支持现有paced计时协议的构建，后续采集始终关闭。成功为BUILD_PASSED/BUILD_ALREADY_PASSED。失败停止，提供build.log、host_tests.log、manifest/request与precision_probe日志，不修改manifest绕校验。

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

RDMA不参与本轮内部因果对照；历史RDMA只作背景参考。候选稳定后再在各自适用内核、相同程序与计时口径下做最终比较。

## 本地验证限制

真实transport主机模拟检查Tail/Head/Unknown在on/off/tail-off下的SDK标志、LL/barrier不变；源检查确认5处角色对应的tail/head地址，剥除角色元数据后原kernel算法语句保持；流程测试检查两门禁、ABC-CBA顺序和失败停止，汇总测试检查三配置身份和角色能力拒绝。开发机没有MetaX GPU，实际MACA编译及GPU正确性必须由服务器构建和门禁验证。
