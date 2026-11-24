# 最新Normal大区间匹配对照运行步骤

2026-11-24口径说明：Combine早期约7ms、近期约133ms的变化仍待解释，见[基线审计](COMBINE_BASELINE_AUDIT_20261124.zh-CN.md)。本主套继续定位Dispatch；Combine原始控制数据保留，但暂不用于计算“Dispatch＋Combine总性能差距”。硬件代码和以下运行五项不变，已开始的测试无需重做。

目的和证据见[实验复盘](NORMAL_REVIEW_20261123.zh-CN.md)。先验证最新URMA tail-off与verbs的整体差距和慢端，再比较同点的GPU发布、CPU读取/提交/完成、接收可见与输出边界。不是继续细拆FADD的351 μs，不跑CQ_READY。

本轮复用经过验证的boundaries稀疏打点，新增的是固定tail-off的参数传递和汇总校验。本地不能运行MetaX GPU，以下仍需真实服务器验证。每后端包含一次128门禁和4096的五个正式case；不存在旧版该组合的完整结果，已有历史全开边界不替代最新tail-off对照。

## 机时覆盖补充：只多跑两个关闭采集的case

既然本轮尚未开始，正式入口改用 `scope coverage`，不再先跑 `scope formal`。唯一增加的硬件实验是连续调用的batch前后控制；FIFO重放只使用原trace日志，不增加GPU采集、构建或服务器运行。

| 顺序 | case | 记录活动 | 必要性 |
| --- | --- | --- | --- |
| 1 | batch_pre | 连续调用，关闭GPU/CPU采集 | 核对更接近原benchmark连续执行形态的时延 |
| 2 | paced | 逐次同步，关闭采集 | 原主线前控制 |
| 3 | trace | 逐次同步，既有稀疏边界 | 原主线阶段定位 |
| 4 | post | 逐次同步，关闭采集 | 原主线后控制，检查扰动和状态漂移 |
| 5 | batch_post | 连续调用，关闭采集 | 判断连续结果是否可复现，避免仅按运行顺序解释差异 |

历史同模块测试中，204从batch到paced曾由约7.985降至6.703 ms，但202同时变慢，不能当整体加速。当前tail-off与verbs还没有这一固定版本的完整对照，因此新增这两个case有明确依据。每项仍为5预热、10计时、丢首值；batch复用既有计时路径，但不等于原benchmark所有默认预热/采样参数。只把相同执行形态的后端结果互相比较。

若两种执行形态的后端差距明显不同，先分析同步节奏和流水线相对位置，不把paced阶段直接外推到连续benchmark。若差距相近，则提高主线阶段观测的适用性。两次控制仍不是长期稳定性证明，不追加长时间随机重跑。

## 1. 两端准备同一提交的独立源码

两端都执行。202设RANK=0，204设RANK=1。当前是URMA内核就用BACKEND=urma；日后RDMA内核窗口用BACKEND=verbs，步骤相同。不要求为了本轮立刻切换内核。两种后端使用同一个提交；第二次拉取若已有新增改动，应仍固定第一次记录的SNAPSHOT，不直接比较不同生产提交。

```bash
cd /home/h00918771/uccl-ep-xpu-integration-new
git pull --ff-only https://github.com/LUOQIHuang/uccl.git codex/metax-urma-integration
set -euo pipefail
REPO="$PWD"
SNAPSHOT=$(git rev-parse HEAD)
printf 'SOURCE_COMMIT=%s\n' "$SNAPSHOT"

BACKEND=urma             # RDMA环境改为verbs
RANK=0                   # 204改为1
JOB="normal_scope_${BACKEND}_01"
export URMA_DIAG_HOME=/home/h00918771
export VERBS_DIAG_HOME="$URMA_DIAG_HOME"
export URMA_DIAG_PYTHON="$URMA_DIAG_HOME/metax_dev/venv-maca-py312/bin/python"
export VERBS_DIAG_PYTHON="$URMA_DIAG_PYTHON"
export MACA_PATH=/opt/maca-3.9.0
export URMA_DIAG_LIBRARY_DIR="$URMA_DIAG_HOME/metax_dev/urma_after_kernel_20261029/diag/umdk_private_618957c3/lib"
export VERBS_DIAG_LIBRARY_DIR="$URMA_DIAG_LIBRARY_DIR"
export URMA_DIAG_INCLUDE_DIR="$URMA_DIAG_HOME/metax_dev/urma_sdk_private_include"
unset FADD_PATH_CAPTURE FADD_PATH_TAIL_SCOPE FADD_PATH_CQ_READY FADD_PATH_CQ_CAMPAIGN

TREE="$URMA_DIAG_HOME/metax_dev/normal_scope_${BACKEND}_src_v1"
BUILD="$URMA_DIAG_HOME/metax_dev/normal_scope_build/${BACKEND}_v1"
ROOT="$URMA_DIAG_HOME/metax_dev/normal_scope_results"
DEST="$REPO/test/reference_events/results/normal_scope_20261123"
FENCE=default
if [ "$BACKEND" = urma ]; then FENCE=peer-tail-off; fi
git worktree add --detach "$TREE" "$SNAPSHOT"
cd "$TREE"
uname -r
mx-smi
```

TREE和BUILD必须为新目录。旧数据保留，不删除、不复用CQ_READY worktree。URMA预期内核为6.6.0-gdr_f00955680_diag；verbs为6.6.0-145.3.34.165.oe2403sp3.aarch64。其余驱动/私有库设置沿用成功环境，脚本只检查，不改内核或硬件。端口33480/33481需可用，两端如需更换则统一修改下方函数。

## 2. 构建和运行

两端分别定义以下函数并构建。GID=3沿用既有verbs实测日志，不用最早表格的GID1替代；设备若已变更，先核实实际可用GID再改两端参数。

```bash
scope() {
  bash test/reference_events/run_boundaries.sh \
    --part "$1" --backend "$BACKEND" --node-rank "$RANK" \
    --master-addr 141.61.17.202 --port 33480 --gid-index 3 \
    --normal-fadd-fence-plan "$FENCE" \
    --build-dir "$BUILD" --job "$JOB" --output "$ROOT" --destination "$DEST"
}

bash test/reference_events/run.sh \
  --backend "$BACKEND" --action build --step trace --reference-profile boundaries \
  --node-rank "$RANK" --job "${JOB}_build" --build-dir "$BUILD" --output "$ROOT" \
  --normal-fadd-fence-plan "$FENCE"
```

构建通过后，两端先运行门禁。每项202先启动，204在180秒内启动同名任务：

```bash
scope smoke
```

两端成功标志均为 `BOUNDARY_SUITE_PASSED ... part=smoke` 后，再各运行：

```bash
scope coverage
```

coverage按顺序运行batch_pre → paced → trace → post → batch_post，每点5预热、10采样丢首值。成功标志为 `BOUNDARY_SUITE_PASSED ... part=coverage`。每项真实Normal正确性检查保留。URMA在全部case固定204 tail-off、202全开，WRITE仍RELAX，FADD仍STRONG。verbs不接受tail-off计划。原formal入口仍保留三项以兼容旧操作，但本轮只执行coverage一次，不将formal和coverage各跑一遍。

不要运行旧CQ_READY、fence ABBA、串行对、tokens或LL扫描。第1节已经清除本轮不使用的FADD_PATH专项变量，不要在运行前重新设置它们。若中途失败，停止后续命令并保留所有已收集目录；不要仅因数据漂移而反复重跑。

## 3. 两端汇齐后的离线汇总

在原仓库运行，与硬件/当前内核无关。两后端的结果各到齐后分别执行，不等待另一后端才能汇总：

```bash
cd "$REPO"
"$URMA_DIAG_PYTHON" tools/reference_events/summarize_boundaries.py \
  --control "$DEST/${JOB}_paced" --trace "$DEST/${JOB}_trace" --post "$DEST/${JOB}_post" \
  --fence-plan "$FENCE" --output "$DEST/${JOB}_summary"
```

复用同一份文件再做两项离线分析，不需要服务器设备。可以由开发侧在收到完整目录后执行：

```bash
"$URMA_DIAG_PYTHON" tools/reference_events/summarize_scope_cadence.py \
  --batch-pre "$DEST/${JOB}_batch_pre" --batch-post "$DEST/${JOB}_batch_post" \
  --paced "$DEST/${JOB}_paced" --post "$DEST/${JOB}_post" \
  --fence-plan "$FENCE" --output "$DEST/${JOB}_cadence"

"$URMA_DIAG_PYTHON" tools/reference_events/audit_boundary_fifo.py \
  --control "$DEST/${JOB}_paced" --trace "$DEST/${JOB}_trace" --post "$DEST/${JOB}_post" \
  --fence-plan "$FENCE" --positions both --output "$DEST/${JOB}_fifo"
```

cadence导出每端两种执行形态的前后均值、漂移和CV；`cadence_max_node_means.csv`是两端本地均值的较大者，不是统一跨机makespan，也不混用为主线逐次同步的逐调用max指标。某一端快16%不能被当成整项任务快16%。

FIFO只对两端均通过该负载控制和时钟筛选的label重放，区分满额、队首WRITE/ATOMIC未取得完成、已完成但尚未退休等状态；同时看已知长尾的第6块和末块，分别导出，不混合占比。GPU在发布tail前CPU可能已读走WRITE，加上采样时钟误差，发布→读取可能非正；此类保留带符号间隔并标记删失，既不报0等待，也不丢弃整组结果。先比较observed/censored覆盖数，再解释正区间内的软件满额比例，不当成整个Dispatch占比。FP8或某项分析失败不抹掉已通过的BF16原始记录，不因此重新跑全部硬件case。独立保存每项分析结果或错误，提供原始目录即可。

输出目录必须是新目录。`usable=False`时也提供全部原始结果，它表示代表性/时钟检查未通过，不等于功能失败。当前summarize_boundaries只做各后端验收；旧compare_matched严格针对全开默认语义，不能拿它直接比较tail-off，不能移除其保护来强行通过。两后端结果到齐后由开发侧按本轮事件定义核对对应通道和请求分布。

### 进一步细化软件边界：不增加硬件case

2026-11-23补充的工具直接复用同一份CPU事件，硬件代码、打点位置和运行五项都不变。已经用5ebc2ac5执行或正在执行的结果也能分析，无需重新编译、重新测试。收到日志后用最新版原仓库运行上述FIFO命令，新增输出 `blocking_state_intervals.csv` 和 `blocking_state_summary.csv`。

| 输出状态 | 严格观测含义 |
| --- | --- |
| credit_available | 软件名额未满；不能据此认定CPU空闲，可能在处理其他队列/请求或受其他条件限制 |
| full_head_pre_submit | 名额满，队首网络请求的首个SDK提交尚未开始 |
| full_head_submission_span | 名额满，队首首次提交开始至最后一次提交结束之间；多次物理提交时不等同单个SDK调用CPU耗时 |
| full_payload_wait_cqe / full_metadata_wait_cqe | 名额满，队首数据/元数据WRITE已提交，应用尚未观测到完整CQE |
| full_atomic_wait_cqe | 名额满，队首ATOMIC已提交，应用尚未观测到完整CQE |
| full_head_cqe_observed_unretired | 队首CQE已经被应用观测，但软件名额尚未释放 |
| full_local_or_untyped_head / full_unresolved_network_head | 本地/未分类控制请求，或缺失可解析的提交边界；明确保留未知，不归成设备等待 |

状态表保留每次队首变化的 `head_wr_id`、请求类别、开始/结束时间、pending数和物理提交次数。每一个可观测的正区间单独按这些边界切开，账目按记录的纳秒单位覆盖其区间并且不重复计算；这是记录单位，不表示GPU边界具有纳秒测量精度。汇总包含所有可观测区间中的零状态、删失数、独立中位数及按区间时长加权份额；独立中位数仍不可相加。所选区间彼此可能重叠，这些份额不是整个Dispatch瓶颈占比。

这样可以具体区分“还没送出去”“提交过程中”“送出后尚无完成观测”“完成已经观测到但回收迟”，并指向实际阻挡的WR。若最大差异在提交前，就不先研究CQE；若在完成后回收，就查Proxy/FIFO；只有提交后且尚无CQE的差异占主导，才进入设备顺序、传输、CQ发布/provider的细拆。

应用日志到这里仍无法区分物理传输、设备内部排队、硬件CQE发布时间与provider尚未交付，不能将wait_cqe直接称为网络传输时间。接收端已有tail/epoch/转发/输出边界继续保留；暂不恢复此前显著扰动性能的逐token/密集GPU区间打点。

## 4. 需要提供的文件

提供完整 `test/reference_events/results/normal_scope_20261123/`，包括两端smoke/batch_pre/paced/trace/post/batch_post自动收集的status、runtime、host、setup、benchmark、api_samples、sample_summary、reference_control、reference_correctness、event_trace及已有summary/cadence/fifo目录。无需为了做离线分析延长设备占用。

另外提供两端构建时ROOT下 `${JOB}_build/console/`，以及BUILD中的build/host_tests日志、build manifest、request及precision_probe相关日志；不需要.so和objects。自动collector在正式case中会收集BUILD日志，但build本身失败时需连同失败目录保存。记录两端SOURCE_COMMIT，确认两个后端采用相同提交。

## 5. 收到数据后的判定顺序

1. 两种无探针执行形态的整体API是否仍有稳定差距，慢端是否仍202；前后漂移是否足以影响判断。
2. 最新URMA第6块和末块命令的发布→读取，以及提交→完成长尾，是否相对verbs显著扩大。
3. 末入站token可见后到Forwarder/Receiver完成是否存在另一段增量；未覆盖区间不强行命名。
4. 只对与结束推进相符的最大候选安排一个单变量干预；局部变好而整体不变，就不继续该方向。

记录精度受采样主机时钟间隙影响。所有比较保持同机时间差，不把两端时间戳相减，也不把并行角色时延堆成100%的加法分解。
