# 下一步只验证一个因果问题：WRITE/FADD混合提交是否导致Normal回退

## 目的、依据和本轮边界

最终目标仍是找出URMA Normal相对verbs慢的具体实现原因，并验证修复能降低真实Dispatch时延。本轮不增加GPU打点，不重跑verbs，不重复WRITE强弱序扫描。

已验收的阶段记录指向BF16提交后长尾：204端payload WRITE的post→CQE P95为verbs 2.290ms、URMA 3.450ms；202端首次WRITE→最后远端token可见跨度为6.428/7.110ms。整体paced的202 BF16为7.149/8.114ms。这些观察尚不证明某个硬件机制，本轮用实际实现干预检验其中一项。

源码存在明确差异：

- ep/src/proxy.cpp的verbs分支先调用post_rdma_async_batched，再调用post_atomic_operations；ep/src/rdma.cpp分别按通道提交WRITE链、独立FADD链。
- URMA分支post_urma_commands把WRITE和独立FADD收进同一批，UrmaTransport::post_batch再按lane提交，所以同lane可能混合两类算子。
- 已上传BF16九个有效窗中，URMA 202/204分别有628/648次SDK调用混合WRITE/FADD，verbs对应调用均为同类链。这说明差异真实发生，而非只看代码提出的空想。

**本轮唯一问题：保留WRITE/FADD算子与依赖，只将URMA改成先提交WRITE阶段、再提交独立FADD阶段，能否使两端配对的BF16 Dispatch至少改善5%？**正结果说明这项提交策略贡献了性能差异；不等于已经证明设备fence、某个内部队列就是唯一根因。

当前机器若仍处于verbs内核，无需再跑RDMA。下面只在已协调好的URMA可用窗口执行，不要求立即切内核。

## 两个版本只差什么

同一个新模块，开关在连接配置时读取：

| 项目 | A：lane-mixed默认 | B：writes-first实验 |
| --- | --- | --- |
| SDK提交阶段 | 每lane可能WRITE+独立FADD混合链 | 已收集WRITE先flush，再收集/提交独立FADD |
| 示例 | lane1[W,F]→lane2[W,F] | lane1[W]→lane2[W]→lane1[F]→lane2[F] |
| WRITE/FADD数量、地址、值 | 相同 | 相同 |
| lane选择、同lane顺序 | 原路径 | 保留 |
| WRITE RELAX、FADD STRONG、原fence、completion all | 保留 | 保留 |
| 显式绑定的WRITE/FADD通知对 | 保持相邻 | 不拆开 |
| quiet/barrier、GPU程序、输入数据 | 原路径 | 保留 |

它改变了提交分组和调用节奏，SDK调用数可能增多；这就是本次检验的策略范围，不宣称只测到了纯设备开销。没有把所有lane遍历细节都改成verbs，控制目标只是两个提交阶段。开关作用于Normal，因此Combine也可能改变，不能将Combine冒充完全不受干预的控制项。LL不参与，默认仍是lane-mixed。

## 固定协议和提前确定的判定

只跑4个双端性能任务，固定ABBA：

1. r01_lane_mixed。
2. r01_writes_first。
3. r02_writes_first。
4. r02_lane_mixed。

每测点5次预热、10次采样、丢首值后9个有效样本；固定tokens4096、hidden7168、experts256、topk8、SM24、Dispatch chunk32、NVL chunk4、每端GPU0一张卡。使用已有paced协议，GPU/CPU事件和主机时钟采集关闭。保留原Normal正确性检查与FP8、BF16、layout、Combine12/16计时。

主要指标是BF16每个配对样本取max(node0,node1)，再求该任务均值；每轮比较B/A。这个指标不等于跨机时钟测得的全局makespan，不会把202快、204变慢误算成整体收益。

| 结果 | 本轮结论和后续动作 |
| --- | --- |
| 两轮B均改善≥5%，各端各组CV≤5%，正确性通过 | 支持提交策略有实质影响；才执行文档后面的2个trace任务，检验提交分组是否改变、完成长尾和可见性区间是否同时收缩 |
| 两轮变化绝对值都<3%，CV≤5% | 本负载未支持它是≥5%的主要优化点；保留默认，不继续增加该开关扫描或机械补轮次 |
| 两轮B均劣化≥5% | 该策略对本负载有负效应，保留默认，不作为优化采用 |
| 正确性/运行失败、CV超标、方向相反或介于阈值之间 | 不采用，不将其解释成根因；提交已有日志，先核查失败/漂移，不自动续跑矩阵 |

FP8和Combine是次要结果。即使BF16改善，也需检查它们是否回退；本轮不会自动修改生产默认。不能拿历史verbs数据作为这次B/A的分母。

## 1. 两端环境设置

先拉取包含本开关的最新分支，下面使用自己的目录。202设RANK=0，204设1：

```bash
cd /home/h00918771/uccl-ep-xpu-integration-new
set -euo pipefail
export URMA_DIAG_HOME=/home/h00918771
export URMA_DIAG_PYTHON=/home/h00918771/metax_dev/venv-maca-py312/bin/python
export MACA_PATH=/opt/maca-3.9.0
export URMA_DIAG_LIBRARY_DIR=/home/h00918771/metax_dev/urma_after_kernel_20261029/diag/umdk_private_618957c3/lib
export URMA_DIAG_INCLUDE_DIR=/home/h00918771/metax_dev/urma_sdk_private_include
# 沿用本机已验证成功的URMA_DIAG_EXTRA_LIBRARY_DIRS；不要改回旧MACA库。
RANK=0  # 204改为1
BUILD=/home/h00918771/metax_dev/reference_event_build/urma_submit_v1
ROOT=/home/h00918771/metax_dev/reference_event_results
JOB=urma_submit_ab_01
DEST="$PWD/test/reference_events/results"
CONSOLE=/home/h00918771/metax_dev/reference_submit_console/$JOB/node$RANK
mkdir -p "$CONSOLE"
uname -r
mx-smi
```

预期内核6.6.0-gdr_f00955680_diag，MACA3.9.0.29/KMD3.10.14；设备udmac0d1e2、GPU 0000:a1:00.0、Atomic Request等条件沿用上一轮URMA成功配置。运行期间GPU/设备应已协调空闲。脚本只检查，不重启、改PCI配置或清理他人进程。

目的：固定环境，避免把运行库、内核或其他负载变化归因于新策略。

## 2. 两端各构建一次新模块

生产Proxy增加了受控分支，必须新BUILD重编译，不能直接复用urma_events_v1，也不要改manifest绕校验。A/B及后续可选trace共用这个新模块：

```bash
bash test/reference_events/run.sh --backend urma --action build --step trace \
  --node-rank "$RANK" --job "${JOB}_build" \
  --build-dir "$BUILD" --output "$ROOT" \
  2>&1 | tee "$CONSOLE/build.log"
```

成功标志BUILD_PASSED或BUILD_ALREADY_PASSED；内部运行CPU模拟/precision probe后构建扩展，不运行GPU benchmark。构建失败停止，提供CONSOLE、BUILD下build.log/host_tests.log、request/manifest及precision_probe日志，不需要.so或objects。

目的：确保实际模块包含开关，且两个策略的二进制相同。

## 3. 两端先检查，不运行benchmark

```bash
bash test/urma_mode_parity/run.sh --action check \
  --backend urma --mode normal --phase-suite --api-only --dispatch-focus \
  --reference-events paced --normal-submit-schedule writes-first \
  --node-rank "$RANK" --job "${JOB}_check" \
  --build-dir "$BUILD" --output "$ROOT" \
  2>&1 | tee "$CONSOLE/check.log"
```

需MODE_PARITY_CHECK_PASSED，runtime中normal_submit_schedule=writes-first、normal_write_order=relax、normal_completion=all、reference_events_version=1。缺字段或不一致会拒绝旧模块。单端check不证明双端通信正确，真实任务仍保留全部正确性检查。

目的：在占用双端运行窗口前发现模块、库和硬件配置错误。

## 4. 双端执行4项性能任务

202先启动，180秒内204启动，JOB相同，仅RANK不同。整套按ABBA顺序运行，不并发：

```bash
bash test/reference_events/run_submit_ab.sh \
  --part performance --node-rank "$RANK" --master-addr 141.61.17.202 \
  --build-dir "$BUILD" --job "$JOB" --port 32980 \
  --output "$ROOT" --destination "$DEST" \
  2>&1 | tee "$CONSOLE/performance.log"
```

32980/32981端口需可用。每项自动check→run→收集；失败仍收集可获得日志并停止，不覆盖已有结果。成功需每项CASE_END normal rc=0，最终SUBMIT_AB_PASSED part=performance。

目的：以实际Dispatch的净变化检验策略，不从时延中扣除新增CPU/SDK成本，不依赖观察器才能完成性能测试。

## 5. 本轮提供哪些日志

提供DEST下4个完整目录，两端node0/node1及build、console都要：

```text
urma_submit_ab_01_r01_lane_mixed/
urma_submit_ab_01_r01_writes_first/
urma_submit_ab_01_r02_writes_first/
urma_submit_ab_01_r02_lane_mixed/
```

套件自动收集setup/preflight/runtime/status、module/build manifest、host/GPU/kernel快照、正确性bench.log、api_samples/sample_summary、reference_control及可获得的build日志；保留原始内容，不只提供均值截图。另外提供CONSOLE下build.log/check.log/performance.log。若构建或check即失败，提供该阶段已有日志即可，不为补齐目录再次启动benchmark。

两端目录齐全后可以离线汇总（输出目录须不存在）：

```bash
"$URMA_DIAG_PYTHON" tools/reference_events/summarize_submit.py \
  --root "$DEST" --job "$JOB" \
  --output "$DEST/${JOB}_summary"
```

输出per_node、paired_calls、rounds、decision及context。保留decision原文；程序只按提前写好的规则分类，不替代实现审查或统计显著性检验。

目的：提供足够的原始证据，能核实对照身份、两端配对和两轮方向，而非再次只确认“整体慢了”。

## 6. 仅在性能支持该方向后采集两个trace任务

先不要执行此节。只有主要结果符合material_improvement，或需要检查开关实际提交形态时，才使用同一BUILD和JOB补这两项；不重跑前4项：

```bash
bash test/reference_events/run_submit_ab.sh \
  --part trace --node-rank "$RANK" --master-addr 141.61.17.202 \
  --build-dir "$BUILD" --job "$JOB" --port 32980 \
  --output "$ROOT" --destination "$DEST" \
  2>&1 | tee "$CONSOLE/trace.log"
```

新增JOB_trace_lane_mixed、JOB_trace_writes_first两个完整目录，也需两端。已有三个GPU事件和CPU timeline足够，不再增加打点。

离线分别与各自r02 paced对照验收采集影响：

```bash
for POLICY in lane_mixed writes_first; do
  "$URMA_DIAG_PYTHON" tools/reference_events/summarize_matched.py \
    --control "$DEST/${JOB}_r02_${POLICY}" \
    --trace "$DEST/${JOB}_trace_${POLICY}" \
    --output "$DEST/${JOB}_trace_${POLICY}_summary"
done
```

目的：确认SDK混合链减少，并检查204完成长尾与202可见性区间是否随净收益一起缩短。若采集不具代表性，前4项性能结果仍独立保留；不让观察器失败阻断Normal性能结论。

## 开发侧已验证的内容

开发机只做主机检查，无法运行MetaX GPU benchmark。测试包含：生产Proxy方法的两阶段提交、两ring请求身份/fence/quiet排空、显式通知对不拆分；实际UrmaTransport配置解析及LL/paired组合拒绝；脚本参数、旧模块拒绝；两端样本配对/预定阈值、源码或boot变化拒绝。CPU SDK测试使用参考SDK类型和模拟硬件，不宣称验证了私有SDK ABI或实机性能。
