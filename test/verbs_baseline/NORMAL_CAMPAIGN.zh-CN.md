# verbs窗口：Normal三组测试一体运行

本轮只使用已协调好的verbs/RDMA内核，不需要URMA设备、私有SDK或切换内核，不运行LL、绑核扫描或chunk大范围调优。目标是补齐当前源码基线、真实Proxy提交/完成数据，以及与URMA split_06对应的WRITE方向/并发对照。

## 1. 三组测试与默认次数

只构建一份**当前源码、USE_URMA=0、USE_DMABUF=1**的扩展，三组共用，旧私有模块不能代替本轮新模块。

| 组别 | 内容 | 双端任务数 | 计时配置 |
| --- | --- | ---: | --- |
| 1：完整Normal | FP8/BF16 Dispatch chunk32，Combine chunk12/16，layout；三轮基线＋一次独立GPU profiler | 4 | 每点预热50次、计时50次，丢弃首个，49次有效 |
| 2：实际Proxy诊断 | 同一真实Normal程序，源码调用点统计WR、SDK post、CQ及Proxy公共阶段、WRITE形状 | 1 | 预热5次、计时5次；仅作诊断，不参与性能基线 |
| 3：WRITE微测 | 复用URMA采集形状；1/4线程，各测双向、202→204、204→202，三轮 | 6 | 每点预热20次、计时50次，丢弃首个 |

共 **11个双端case**。第三组每个case含FP8/BF16两种形状×三个方向，共6点；每次总共128条WRITE，1线程每线程128条、4线程每线程32条。每线程8条RC数据QP，每QP每批4条WRITE，提交一批后排空全部CQE，再发下一批。源数据双bank交替，接收槽在双端完成并通过每次GPU校验后才复用。

GPU profiler与CPU诊断不同时开启。组1基线中的console notify是保留原调优流程的占位值，不是GPU阶段；真正GPU分段看独立profiler的 `gpu_stages.jsonl`、`gpu_trace_*.json`。

## 2. 两端设置环境

202（141.61.17.202）使用RANK0，204（141.61.17.204）使用RANK1。两端更新代码后，在各自Bash终端执行，仅修改RANK：

```bash
cd /home/h00918771/uccl-ep-xpu-integration-new
set -euo pipefail
export VERBS_DIAG_HOME=/home/h00918771
export VERBS_DIAG_PYTHON=/home/h00918771/metax_dev/venv-maca-py312/bin/python
export MACA_PATH=/opt/maca-3.7.0

RANK=0  # 204改为1
BUILD=/home/h00918771/metax_dev/verbs_baseline_build/normal_campaign_v1
JOB=verbs_normal_campaign_01
RESULTS=/home/h00918771/metax_dev/verbs_campaign_results/$JOB
CONSOLE=/home/h00918771/metax_dev/verbs_campaign_console/$JOB
LAYOUT="$PWD/test/urma_mode_parity/results/urma_normal_data_01/urma_normal_data_01_layout/node${RANK}/normal/write_layout.jsonl"
GID=3
mkdir -p "$CONSOLE"
test -s "$LAYOUT"
```

这里的LAYOUT只是仓库中的历史数据输入，不会运行URMA或加载URMA模块。当前采集选择的FP8/BF16形状为237056B/459264B、源/目标地址余数0/0。两端都保存本端输入，微测实际使用node0分享的同一计划。

保留此前已通过的 `VERBS_DIAG_EXTRA_LIBRARY_DIRS`（若有）。不要换用系统Python。构建需要已有MetaX Torch、cu-bridge、nanobind、C++17编译器以及libibverbs开发头文件/库；不需要私有URMA头文件。

## 3. 确认HCA与GID

本轮固定HCA `rocep40s0f0`、port1，TCP控制接口沿用 `enp33s0`。默认GID=3来自此前EP自动选择记录，**不是独立perftest表格中的GID=1**。本轮会把同一个显式GID传给完整benchmark和微测，避免二者偷偷使用不同路径。

两端分别检查：

```bash
ibv_devinfo -d rocep40s0f0
cat /sys/class/infiniband/rocep40s0f0/ports/1/gids/$GID
cat /sys/class/infiniband/rocep40s0f0/ports/1/gid_attrs/types/$GID
cat /sys/class/infiniband/rocep40s0f0/ports/1/gid_attrs/ndevs/$GID
```

必须为非零、可互通的RoCE v2 GID，port为ACTIVE。预检查会拒绝零GID或非v2项，但仅凭类型不能证明两端路由可用。如果当前环境的实际可用项不是3，先查看GID表，再把两端GID设为实际对应的同一个索引，后续命令始终使用该变量：

```bash
for ITEM in /sys/class/infiniband/rocep40s0f0/ports/1/gid_attrs/types/*; do
  INDEX=${ITEM##*/}
  printf 'index=%s type=' "$INDEX"
  cat "$ITEM"
  cat "/sys/class/infiniband/rocep40s0f0/ports/1/gids/$INDEX"
done
```

不修改网卡/GID配置，不把141.61控制地址与RoCE GID必须相同当作要求；数据地址以实际RDMA网络为准。第三组记录active MTU，SL=3、TC=104、PSN=0，独立RC QP建连；这些参数另存 `verbs_path.json`，不是宣称与独立perftest配置完全相同。

## 4. 两端各自构建与检查

本轮新增源码诊断与微测绑定，必须重新编译到新BUILD目录：

```bash
bash test/verbs_baseline/run.sh --action build --variant current \
  --node-rank "$RANK" --job verbs_campaign_build --build-dir "$BUILD" \
  --mode normal --gid-index "$GID" \
  2>&1 | tee "$CONSOLE/build_node${RANK}.log"

bash test/verbs_baseline/run.sh --action check --variant current \
  --node-rank "$RANK" --job verbs_campaign_check --build-dir "$BUILD" \
  --mode normal --gid-index "$GID" --campaign-check \
  2>&1 | tee "$CONSOLE/check_node${RANK}.log"
```

两端应分别出现 `VERBS_BUILD_PASSED`、`VERBS_CHECK_PASSED`。runtime应有 `verbs_diag_version: 1`、`verbs_micro: true`，纯verbs扩展若加载liburma会被拒绝。检查不要求同时开始。

构建先跑CPU测试：使用真实verbs头文件、模拟provider验证源码post/poll统计，检查原生微测host语法，再编译MetaX扩展。开发机没有MetaX GPU，真实cu-bridge编译和双机性能仍须服务器验证。

构建失败就停止，保留 `host_tests.log`、`build.log`、`request.json` 和终端日志。输入未变可重试同一命令；源码/编译参数变化换新BUILD目录，不手改manifest。

## 5. 双端执行整个套件

202先启动，随后180秒内在204执行同一条命令；JOB、端口和GID相同，RANK不同：

```bash
bash test/verbs_baseline/run_normal_campaign.sh \
  --node-rank "$RANK" --master-addr 141.61.17.202 \
  --job "$JOB" --build-dir "$BUILD" --layout-file "$LAYOUT" \
  --gid-index "$GID" --micro-cpus auto --port 31780 --result-root "$RESULTS" \
  2>&1 | tee "$CONSOLE/suite_node${RANK}.log"
```

31780用于任务握手，31781用于torchrun或微测TCP控制，均需可用。套件会先完成完整Normal基线和profiler，再跑CPU诊断，最后跑微测。微测线程顺序为r01的1→4、r02的4→1、r03的1→4。

组1应有4个 `VERBS_CASE_END ... rc=0`；组2有1个；每个微测应有6条 `VERBS_MICRO_POINT`、`VERBS_MICRO_ALL_PASSED`，整个套件最终两端均出现 `VERBS_NORMAL_CAMPAIGN_ALL_PASSED`。

完整Normal沿用之前基线设置，关闭adaptive sleep和Proxy绑核；不同时改变生产调度。微测为与URMA split_06匹配而单独固定CPU：优先GPU所在NUMA节点的四个可用CPU，另选同节点第五个CPU给Python控制进程；GPU NUMA未知时记录fallback原因。实际选择保存于 `affinity.json`，每个C++发送线程在计时前设置并核对单核亲和性。

若同事已分配固定CPU，可将 `--micro-cpus auto` 改为例如 `--micro-cpus 192,193,194,195`，实际数字必须是本机允许、同NUMA节点的四个独立CPU，且同节点还需一个可用控制核。两端编号允许不同，各自三轮保持相同。

发生任何失败立即收集两端结果，不继续后面的任务，不自动删除共享内存或重置设备。重跑换新JOB。单向微测的接收端也必须正常参与，不能只启动发送端。

## 6. 三组数据分别能说明什么

**组1**用于完整调用时延和有效带宽，是当前源码的正式性能基线。GPU profiler另跑，持续时间包含kernel内部等待，不代表纯计算或纯网络时间。

**组2**在真实源码调用点覆盖 `ibv_post_send` 和普通 `ibv_poll_cq`，无需LD_PRELOAD。记录公共队列收集、Proxy批处理、WRITE准备范围、SDK提交、CQ回收范围、GPU确认，以及WRITE/FADD物理WR数量、字节、QP和地址对齐分布；每类约1/64抽样登记“提交准备→CQE被软件观察”的时长。它包含追踪、提交、排队、设备和CPU发现时间，不是独立硬件服务时间。

只有诊断任务启用这些计数；正式基线默认关闭。CPU范围跨线程且相互嵌套，不能相加成端到端时延，也不能把空轮询比例直接当CPU利用率。DMA-BUF跨MR拆分会产生多个物理WR，只有有SIGNAL的WR登记完成；`retired_logical_ops`这一共用字段在verbs侧表示成功关联的signaled完成记录，不能直接等同于URMA逻辑命令数。`verbs_untracked_boundary_cqes`保留窗口边界/未覆盖控制完成，不直接当硬件错误；post、完成状态错误和跟踪溢出则拒绝有效诊断。verbs形状JSON中的 `lane` 实际是qp_num，不能按其数值对应URMA lane索引。

这个版本针对当前非EFA RoCE、原生FADD路径；EFA的扩展WR接口没有完整覆盖。汇总会要求确实观测到WRITE及FADD，否则不能把它当本轮要求的硬件原子诊断。

**组3**使用生产 `reg_mr_gpu_dmabuf()` 和GPU分配/校验帮助函数，严格DMA-BUF，无peermem/host fallback；上下文、PD和主MR在本机线程间共享，每线程独立CQ和8条数据QP。它绕过GPU命令ring、Proxy反馈和完整kernel，测试模式与URMA split_06的WRITE-only部分一致，但不是复现整个生产调用。所有WRITE单独请求CQE，不添加FADD。微测会检查实际构造的源/目标地址余数，接收端每次在计时外校验GPU字节。

单向接收端的空操作耗时不是网络时延，不显示其吞吐。吞吐按本端发出的128条payload字节/本端host完成区间计算；不是网卡线速，也不与组1按逻辑token字节计算的有效带宽混用。1/4线程总发送量相同，但总QP数量和槽工作集不同。微测是批次排空方式，不能称为连续补充的峰值带宽。不同内核、网络和注册栈仍需分别记录，微测差异本身不能直接证明完整Dispatch根因。

组2仍未给出GPU发布命令→CPU发现的跨时钟精确延迟，也未直接测出远端GPU可见性；本轮交付是可比的CPU/提交/完成证据和GPU粗阶段，不宣称已获得完整可相加的关键路径分解。

## 7. 两端分别整理日志

```bash
DEST="$PWD/test/verbs_baseline/results/$JOB"
"$VERBS_DIAG_PYTHON" test/verbs_baseline/collect_campaign.py \
  --root "$RESULTS" --job "$JOB" --node-rank "$RANK" --destination "$DEST"

mkdir -p "$DEST/console/node${RANK}" "$DEST/build/node${RANK}"
cp -v "$CONSOLE"/*.log "$DEST/console/node${RANK}/"
for NAME in host_tests.log build.log request.json manifest.json; do
  if [ -f "$BUILD/$NAME" ]; then
    cp -v "$BUILD/$NAME" "$DEST/build/node${RANK}/"
  fi
done
```

构建就失败、没有任务时，collect会提示没有结果，直接从mkdir起整理构建日志即可。

需要提供 **`test/verbs_baseline/results/$JOB/` 完整目录，两端node0/node1都要有**：

- baseline任务的三轮原始 `api_samples.jsonl`、`sample_summary.jsonl`、`status.json`；profiler的 `gpu_stages.jsonl`、全部 `gpu_trace_*.json`。
- cpu任务的 `verbs_phases.jsonl`、`write_layout.jsonl`、原始样本、status和bench.log。
- 六个micro任务的 `request.json`、`plan.json`、`layout_input.jsonl`、`descriptors.json`、`verbs_path.json`、`affinity_pair.json`、`micro_samples.jsonl`、`micro_summary.jsonl`、status和bench.log。
- 各任务的setup、host、module_manifest/runtime、affinity（微测）、实际benchmark源码（完整测试）、RDMA端口/GID及设备信息、当次kernel/计数器前后快照、空闲检查和失败错误文件。微测另保留测量后、清理前的kernel边界。
- console与build目录中的上述日志和manifest。无需 `.so`、objects、cache、共享内存文件。

## 8. 两端数据齐全后汇总

```bash
"$VERBS_DIAG_PYTHON" test/verbs_baseline/summarize_campaign.py \
  "test/verbs_baseline/results/$JOB" \
  --output "test/verbs_baseline/results/$JOB/summary"
```

应出现 `VERBS_CAMPAIGN_SUMMARY_PASSED`。缺任一端、case失败、源码/内核/boot/GID变化、缺三轮或原始样本、CQE/数据量不匹配、诊断错误/溢出均会拒绝。结果包括 `baseline/` 下完整时延和GPU粗阶段CSV，以及 `verbs_cpu_stages.csv`、`verbs_micro_runs.csv`、`verbs_micro_aggregate.csv`。不要把CPU诊断计时并入正式基线。

数据回来后优先回答三件事：当前完整verbs基线是否仍明显快于URMA；提交/完成统计在哪个对应环节出现差异；verbs在相同WRITE负载下是否也出现相同双向降速。再决定下一项生产优化，避免继续无目的扩大微测矩阵。
