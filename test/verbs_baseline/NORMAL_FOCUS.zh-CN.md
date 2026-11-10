# 当前发行版内核窗口：通知四点＋轻量CPU对照

本轮只测verbs，目标是补齐已有urma_notify_05的对照缺口。默认复用已记录编译输入的664c8193 base模块，不编译、不运行GPU阶段插桩，不切内核、不修改PCI/驱动/CPU频率、不kill其他用户进程。仍须协调GPU及网络设备空闲。

## 1. 五个双端任务

| 任务 | 设置 | 回答的问题 |
| --- | --- | --- |
| notify_r01/r02/r03 | 每轮FP8/BF16两形状×WRITE、FADD、WRITE+FADD、GPU观察通知四点；预热20、采样50、首个丢弃 | 数据、原子通知、组合依赖、GPU观察路径的差异集中在哪类操作 |
| baseline | Normal五个固定测点；4096 tokens、hidden7168、experts256、topk8；预热50、采样50、首个丢弃 | 此轮未经CPU统计的真实API时延 |
| cpu | 同一模块、负载和采样，开启轻量CPU累计统计 | 请求/字节/批次数、准备、提交及完成观察统计；同时检查统计扰动 |

微测固定4线程、每线程8条lane、窗口1、双向，每迭代32条WRITE或FADD，组合点各32条。C/D每lane的WRITE与FADD在同一RC QP，等待全部本地CQE；D另检查远端GPU对计数的观察。源/目标与通知均沿用原生DMA-BUF GPU路径，FADD旧值写回host缓冲区；每轮校验payload与计数，失败停止。D使用已有小型flag观察kernel，不是33点GPU插桩。

直接读取已上传URMA的plan：FP8每条WRITE237056B、BF16每条459264B，src/dst对齐偏移均0。保存计划SHA并冻结副本，两端一致性检查；不重新抓布局。每个微测轮次轮换四点顺序，三轮独立进程，不把两节点合并成六轮。

Normal保留原正确性检查及layout、FP8/BF16 Dispatch chunk32、Combine chunk12/16。重点看Dispatch和Combine16；chunk12仍保留作为固定配置参照。不开Kineto、GPU源码计时、逐请求timeline或WRITE布局日志；轻量CPU统计仍可能扰动执行。

## 2. 复用旧base的校验规则

使用--variant archived时，原BUILD下的manifest必须满足：

- revision=664c8193134bbd360caea322e2ec6a0630bfd4d5；sources和flags逐项与仓库已通过verbs_stage_01基线的记录一致。
- 二进制SHA与原manifest一致；USE_URMA=0、USE_DMABUF=1、UCCL_GPU_STAGE_TRACE=0。
- 运行时API具有verbs_diag_version=1、verbs_notify_version=1及VerbsWriteSession，gpu_stage_version=0、queue_backend=fifo，不加载liburma。
- benchmark的四份Python文件从该Git提交冻结；当前诊断worker与其分开记录，绝不把旧.so标成当前源码编译。

202参考模块SHA为c9c647ff644983a0c3ba03b9bac95ce3cb46c1b01c6c3ef621bc1aabb00ae985。另一端的二进制可与参考SHA不同，但必须与它自己的原manifest一致，且编译输入必须与参考记录相同；实际硬件语义仍由本轮正确性检查验证。

不改manifest、不关闭哈希校验、不接受任意旧.so。若不满足上述条件，check即停止；先提供日志和该BUILD的manifest，不立即发起完整重编译。此入口只固定目前发行版内核的已验证配方，不代表其他内核技术上不支持verbs。

## 3. 两端环境

两端更新同一版本。在Bash执行，202 RANK0、204 RANK1：

```bash
cd /home/h00918771/uccl-ep-xpu-integration-new
set -euo pipefail
export VERBS_DIAG_HOME=/home/h00918771
export VERBS_DIAG_PYTHON=/home/h00918771/metax_dev/venv-maca-py312/bin/python
# 保持当前已使用的运行库组合，不在本轮升级/切换。
export MACA_PATH=/opt/maca-3.9.0
RANK=0  # 204改为1
GID=3
PY="$VERBS_DIAG_PYTHON"
BUILD=/home/h00918771/metax_dev/verbs_baseline_build/stage_base_v1
JOB=verbs_normal_focus_01
ROOT=/home/h00918771/metax_dev/verbs_normal_focus_results/$JOB
PLAN="$PWD/test/urma_mode_parity/results/urma_notify_05/urma_notify_05_micro_r01/node0/micro/plan.json"
test -s "$BUILD/manifest.json"
test -s "$BUILD/lib/ep.abi3.so"
test -s "$PLAN"
uname -r
df -h / "$BUILD"
```

沿用此前通过的VERBS_DIAG_EXTRA_LIBRARY_DIRS（若有）。默认HCA rocep40s0f0、port1、控制接口enp33s0、GPU0、GID3；GID必须是非零、可互通的RoCE v2项。不要改用trace模块或旧ep_host_atomic模块。若本机旧base不在上述目录，BUILD改为原manifest所在目录即可，但校验规则不变。

## 4. 两端先各自check，无需同时启动

```bash
bash test/verbs_baseline/run_normal_focus.sh --action check \
  --node-rank "$RANK" --job "$JOB" --build-dir "$BUILD" \
  --output "$ROOT" --gid-index "$GID" --matched-plan "$PLAN"
```

成功标志NORMAL_FOCUS_CHECK_PASSED；此阶段不启动benchmark、不编译。失败提供$ROOT/$JOB/console/node*/check.log和原BUILD/manifest.json。ARCHIVE_INPUTS_NOT_PINNED_BASE表示不是指定编译输入；ARCHIVE_MODULE_SHA_MISMATCH表示.so与原manifest不一致；CURRENT_BUILD_MISMATCH用于current构建，不能靠编辑manifest解决。

## 5. 双端运行

202先启动，180秒内在204启动相同命令，JOB/端口/GID一致：

```bash
bash test/verbs_baseline/run_normal_focus.sh --action run --part all \
  --node-rank "$RANK" --master-addr 141.61.17.202 \
  --job "$JOB" --build-dir "$BUILD" --output "$ROOT" \
  --gid-index "$GID" --matched-plan "$PLAN" --micro-cpus auto --port 32680
```

需要32680/32681可用。微测每轮应8条VERBS_MICRO_POINT、VERBS_MICRO_ALL_PASSED；Normal两个任务应CASE_END rc=0，两端最终NORMAL_FOCUS_ALL_PASSED。

微测auto选择GPU NUMA节点上的四个可用worker核和独立controller核，保存并在线程中校验。若已协调固定CPU，用--micro-cpus填写本机四个核，尽量沿用URMA微测的放置；两后端放置不同时比较结果会标记。完整Normal沿用此前关闭Proxy绑核的设置，不混入新的调优变量。

窗口短可先--part notify，只完成前三个任务并立即收集；之后用同一JOB、ROOT改--part normal，只完成后两个。不要对已有成功目录再次运行同一part；失败重跑换JOB尾号。支持--variant current用于另行验证的新base，但默认archived不自动构建；不要把两种variant混在同一JOB。

## 6. 收集两端日志；失败也收集

```bash
DEST="$PWD/test/verbs_baseline/results/$JOB"
if compgen -G "$ROOT/${JOB}_*/node${RANK}" >/dev/null; then
  "$PY" test/verbs_baseline/collect_campaign.py --root "$ROOT" --job "$JOB" \
    --node-rank "$RANK" --destination "$DEST"
fi
"$PY" test/gpu_stage_recovery/collect.py "$ROOT/$JOB" \
  --node-rank "$RANK" --destination "$DEST/$JOB"
```

提供test/verbs_baseline/results/$JOB完整目录，五个任务各自的node0/node1，以及console。包括setup、host、module_manifest、runtime、benchmark、status/bench、微测request/plan/matched_plan/affinity/native metadata/原始samples和summary、Normal api_samples/sample_summary/CPU phases，以及内核/计数器快照。不要.so、objects、cache、vmcore；脚本已排除这些文件。仅check失败时也能收集console，不必为补日志重跑。

## 7. 汇总和已有URMA比较

两端结果齐全后：

```bash
"$PY" test/verbs_baseline/summarize_normal_focus.py \
  --root "$DEST" --job "$JOB" --output "$DEST/summary" \
  --urma-results test/urma_mode_parity/results/urma_notify_05
```

只完成微测时增加--part notify；只完成Normal可用--part normal（无需--urma-results）。全部完成后重新用默认all汇总。成功标志NORMAL_FOCUS_SUMMARY_PASSED。

输出notifications下的三轮微测原始汇总/中位数与范围；normal下的API均值、CV、CPU统计变化百分比及每API归一化CPU scopes；另有notification_comparison.csv和comparison_context.json。比较URMA原始计数/采样及验证状态，几何不匹配、错误轮次、身份或boot变化会拒绝。placement_matches记录CPU放置是否一致。

**结论边界：** 历史URMA与此次verbs使用不同内核/构建/可能不同驱动环境。URMA的WRITE relax、FADD strong且per-request fence；verbs原生FADD无SEND_FENCE，不人为改成相同标志。四点匹配payload和协议，但这些差异必须保留。C-A或D-C不能直接解释为可相加的单阶段时延。

一轮Normal基线仅用于本次CPU观测的邻近对照，不替代已有三轮稳定性结果；变化超过5%的perturbed只是筛选标记，也可能包含运行状态漂移，需要时再补一轮未统计回测，不预先重复三轮。

数据将用于区分数据写入、原子/顺序依赖、GPU观察及完整程序排队/背压候选；只有后续单变量改动能回收整体时延差距，才进一步做因果归因。
